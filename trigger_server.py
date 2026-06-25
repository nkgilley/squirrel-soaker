#!/usr/bin/env python3
# trigger_server.py
# HTTP server running on the Raspberry Pi 3 to trigger the solenoid and sync backlog files.
# Compatible with Python 3.4.2+.

import os
import sys
import time
import datetime
import subprocess
import threading
import shutil
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Warning: RPi.GPIO library not found. GPIO triggers will be simulated.")
    GPIO = None

PORT = 8080
SOLENOID_PIN = 17
DEFAULT_SPRAY_DURATION = 3.0
MAC_IP = '192.168.86.137'
CAPTURES_DIR = os.path.expanduser('~/squirrel_soaker/captures')
VIDEO_TMP_DIR = '/dev/shm/squirrel_soaker'
BACKLOG_MIN_AGE_SECONDS = 45
VIDEO_START_LEAD_SECONDS = 1.0
VIDEO_POST_ROLL_SECONDS = 1.0
sync_lock = threading.Lock()

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def get_local_time_and_defaults():
    try:
        import capture
        local_time = capture.get_eastern_time()
        default_rot = getattr(capture, 'ROTATION', 0)
        default_roi = getattr(capture, 'VIDEO_ROI', getattr(capture, 'ROI', None))
    except Exception as e:
        print("[Video] Warning: could not import capture config: {0}".format(e))
        local_time = datetime.datetime.now()
        default_rot = 0
        default_roi = None
    return local_time, default_rot, default_roi

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def find_camera_video_command():
    for binary in ('rpicam-vid', 'libcamera-vid', 'raspivid'):
        path = shutil.which(binary)
        if path:
            return binary
    return 'raspivid'

def build_video_command(duration_ms, filepath, rotation=None, roi=None):
    camera_cmd = find_camera_video_command()
    if camera_cmd in ('rpicam-vid', 'libcamera-vid'):
        cmd = [
            camera_cmd,
            "--timeout", str(duration_ms),
            "--width", "1280",
            "--height", "720",
            "--output", filepath,
            "--codec", "h264",
            "--nopreview"
        ]
        if rotation in [0, 180]:
            cmd.extend(["--rotation", str(rotation)])
        elif rotation in [90, 270]:
            print("[Video] Warning: {0} only supports rotation 0 or 180; ignoring rotation {1}.".format(camera_cmd, rotation))
        if roi:
            cmd.extend(["--roi", roi])
        return cmd

    cmd = ["raspivid", "-t", str(duration_ms), "-w", "1280", "-h", "720", "-o", filepath]
    if rotation in [90, 180, 270]:
        cmd.extend(["-rot", str(rotation)])
    if roi:
        cmd.extend(["-roi", roi])
    return cmd

def record_video(duration_ms=5000, rotation=None, roi=None):
    import urllib.parse

    local_time, default_rot, default_roi = get_local_time_and_defaults()
    rot = rotation if rotation is not None else default_rot
    selected_roi = roi if roi is not None else default_roi

    ensure_dir(VIDEO_TMP_DIR)

    filename = "vid_{0}.h264".format(local_time.strftime("%Y%m%d_%H%M%S"))
    filepath = os.path.join(VIDEO_TMP_DIR, filename)

    cmd = build_video_command(duration_ms, filepath, rotation=rot, roi=selected_roi)

    print("[Video] Recording {0}s video to RAM at {1}... (rotation={2}, roi={3})".format(duration_ms / 1000.0, filepath, rot, selected_roi))
    try:
        timeout_seconds = int(duration_ms / 1000.0) + 10
        subprocess.check_call(cmd, timeout=timeout_seconds)
        print("[Video] Finished recording in RAM: {0}".format(filepath))

        encoded = urllib.parse.quote(filename)
        url = "http://{0}:5001/api/upload_video?filename={1}".format(MAC_IP, encoded)
        try:
            post_file(url, filepath, 'video/h264', timeout=30)
            os.remove(filepath)
            print("[Video] Uploaded {0} from RAM and removed local copy.".format(filename))
        except Exception as e:
            ensure_dir(CAPTURES_DIR)
            backlog_path = os.path.join(CAPTURES_DIR, filename)
            shutil.move(filepath, backlog_path)
            print("[Video] Upload failed ({0}); saved video to SD backlog: {1}".format(e, backlog_path))
    except subprocess.TimeoutExpired:
        print("[Video] Error recording video: camera command timed out after {0}s".format(timeout_seconds))
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
    except Exception as e:
        print("[Video] Error recording video: {0}".format(e))
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass

def post_file(url, filepath, content_type, timeout=20):
    import urllib.request

    with open(filepath, 'rb') as f:
        data = f.read()
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': content_type},
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        response.read()

def sync_backlog():
    import urllib.parse

    if not sync_lock.acquire(False):
        print("[Sync] Sync already in progress; skipping overlapping request.")
        return

    try:
        if not os.path.exists(CAPTURES_DIR):
            print("[Sync] No SD backlog directory found; nothing to sync.")
            return

        uploaded = 0
        failed = 0
        print("[Sync] Scanning for backlog files in {0}...".format(CAPTURES_DIR))

        for filename in sorted(os.listdir(CAPTURES_DIR)):
            filepath = os.path.join(CAPTURES_DIR, filename)
            if not os.path.isfile(filepath):
                continue
            if time.time() - os.path.getmtime(filepath) < BACKLOG_MIN_AGE_SECONDS:
                continue

            try:
                lower = filename.lower()
                if lower.endswith('.jpg') or lower.endswith('.jpeg'):
                    url = "http://{0}:5001/api/predict".format(MAC_IP)
                    post_file(url, filepath, 'image/jpeg', timeout=20)
                elif lower.endswith('.h264'):
                    encoded = urllib.parse.quote(filename)
                    url = "http://{0}:5001/api/upload_video?filename={1}".format(MAC_IP, encoded)
                    post_file(url, filepath, 'video/h264', timeout=30)
                else:
                    continue

                os.remove(filepath)
                uploaded += 1
                print("[Sync] Uploaded and removed {0}".format(filename))
            except Exception as e:
                failed += 1
                print("[Sync] Failed to upload {0}: {1}".format(filename, e))

        print("[Sync] Done. uploaded={0}, failed={1}".format(uploaded, failed))
    finally:
        sync_lock.release()

class TriggerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print("[Server] " + (format % args))

    def do_POST(self):
        from urllib.parse import urlparse, parse_qs
        parsed_path = urlparse(self.path)

        if parsed_path.path == '/spray':
            duration = DEFAULT_SPRAY_DURATION
            rotation = None
            roi = None
            query = parse_qs(parsed_path.query)

            if 'duration' in query:
                try:
                    duration = float(query['duration'][0])
                except ValueError:
                    pass
            if 'rotation' in query:
                try:
                    rotation = int(query['rotation'][0])
                except ValueError:
                    pass
            if 'roi' in query:
                roi = query['roi'][0].strip()

            print("Activating solenoid on GPIO {0} for {1}s... (rotation={2}, roi={3})".format(
                SOLENOID_PIN, duration, rotation, roi
            ))

            video_duration_seconds = max(1.0, duration + VIDEO_POST_ROLL_SECONDS)
            video_duration_ms = int(video_duration_seconds * 1000)
            video_thread = threading.Thread(target=record_video, args=(video_duration_ms, rotation, roi))
            video_thread.daemon = True
            video_thread.start()
            if VIDEO_START_LEAD_SECONDS > 0:
                print("[Video] Giving camera {0:.1f}s head start before solenoid.".format(VIDEO_START_LEAD_SECONDS))
                time.sleep(VIDEO_START_LEAD_SECONDS)

            if GPIO:
                GPIO.output(SOLENOID_PIN, GPIO.HIGH)
                time.sleep(duration)
                GPIO.output(SOLENOID_PIN, GPIO.LOW)
            else:
                time.sleep(duration)
                print("(Simulation) Solenoid activated and deactivated.")

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"success","message":"solenoid triggered"}')
        elif parsed_path.path == '/sync':
            sync_thread = threading.Thread(target=sync_backlog)
            sync_thread.daemon = True
            sync_thread.start()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"success","message":"sync started"}')
        else:
            self.send_response(404)
            self.end_headers()

def run():
    if GPIO:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(SOLENOID_PIN, GPIO.OUT)
        GPIO.output(SOLENOID_PIN, GPIO.LOW)
        print("GPIO initialized successfully.")

    server_address = ('', PORT)
    httpd = HTTPServer(server_address, TriggerHandler)
    print("Solenoid trigger server listening on port {}...".format(PORT))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping trigger server.")
    finally:
        if GPIO:
            GPIO.cleanup()
            print("GPIO cleaned up.")

if __name__ == '__main__':
    run()
