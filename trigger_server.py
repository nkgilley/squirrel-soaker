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
import fcntl
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Warning: RPi.GPIO library not found. GPIO triggers will be simulated.")
    GPIO = None

PORT = 8080
SOLENOID_PIN = 17
BUTTON_PIN = int(os.environ.get('BUTTON_PIN', '27'))
BUTTON_ACTIVE_LOW = os.environ.get('BUTTON_ACTIVE_LOW', 'true').lower() not in ('0', 'false', 'no', 'off')
BUTTON_BOUNCE_SECONDS = 0.75
BUTTON_POLL_SECONDS = 0.05
DEFAULT_SPRAY_DURATION = 3.0
MAC_IP = '192.168.86.137'
CAPTURES_DIR = os.path.expanduser('~/squirrel_soaker/captures')
VIDEO_TMP_DIR = '/dev/shm/squirrel_soaker'
BACKLOG_MIN_AGE_SECONDS = 45
BACKLOG_MAX_FILES = 300
BACKLOG_MAX_BYTES = 250 * 1024 * 1024
BACKLOG_MAX_AGE_SECONDS = 24 * 60 * 60
VIDEO_START_LEAD_SECONDS = 1.0
VIDEO_POST_ROLL_SECONDS = 1.0
CAMERA_LOCK_FILE = '/tmp/squirrel_soaker_camera.lock'
sync_lock = threading.Lock()
spray_lock = threading.Lock()

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

def get_backlog_files():
    files = []
    if not os.path.exists(CAPTURES_DIR):
        return files
    for filename in os.listdir(CAPTURES_DIR):
        lower = filename.lower()
        if not (lower.endswith('.jpg') or lower.endswith('.jpeg') or lower.endswith('.h264')):
            continue
        path = os.path.join(CAPTURES_DIR, filename)
        if not os.path.isfile(path):
            continue
        try:
            stat = os.stat(path)
            files.append({
                'path': path,
                'filename': filename,
                'mtime': stat.st_mtime,
                'size': stat.st_size
            })
        except Exception:
            pass
    files.sort(key=lambda item: item['mtime'])
    return files

def prune_backlog(reason='capacity'):
    now = time.time()
    files = get_backlog_files()
    removed = 0
    removed_bytes = 0

    for info in list(files):
        if now - info['mtime'] <= BACKLOG_MAX_AGE_SECONDS:
            continue
        try:
            os.remove(info['path'])
            removed += 1
            removed_bytes += info['size']
            files.remove(info)
        except Exception as e:
            print("[Backlog] Could not remove expired {0}: {1}".format(info['filename'], e))

    total_bytes = sum(info['size'] for info in files)
    while files and (len(files) > BACKLOG_MAX_FILES or total_bytes > BACKLOG_MAX_BYTES):
        info = files.pop(0)
        try:
            os.remove(info['path'])
            removed += 1
            removed_bytes += info['size']
            total_bytes -= info['size']
        except Exception as e:
            print("[Backlog] Could not prune {0}: {1}".format(info['filename'], e))

    if removed:
        print("[Backlog] Pruned {0} old backlog files ({1} bytes) because {2}.".format(removed, removed_bytes, reason))
    return removed

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

def record_video(duration_ms=5000, rotation=None, roi=None, started_event=None):
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
        lock_file = open(CAMERA_LOCK_FILE, 'w')
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            if started_event:
                started_event.set()
            subprocess.check_call(cmd, timeout=timeout_seconds)
        finally:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
            finally:
                lock_file.close()
        print("[Video] Finished recording in RAM: {0}".format(filepath))

        encoded = urllib.parse.quote(filename)
        url = "http://{0}:5001/api/upload_video?filename={1}".format(MAC_IP, encoded)
        try:
            post_file(url, filepath, 'video/h264', timeout=30)
            if os.path.exists(filepath):
                os.remove(filepath)
            print("[Video] Uploaded {0} from RAM and removed local copy.".format(filename))
        except Exception as e:
            ensure_dir(CAPTURES_DIR)
            prune_backlog('before saving a failed video upload')
            backlog_path = os.path.join(CAPTURES_DIR, filename)
            shutil.move(filepath, backlog_path)
            print("[Video] Upload failed ({0}); saved video to SD backlog: {1}".format(e, backlog_path))
            prune_backlog('after saving a failed video upload')
    except subprocess.TimeoutExpired:
        print("[Video] Error recording video: camera command timed out after {0}s".format(timeout_seconds))
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
    except Exception as e:
        if started_event:
            started_event.set()
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

def report_manual_spray(duration):
    import json
    import urllib.request

    payload = {
        'type': 'manual',
        'duration': duration,
        'source': 'button'
    }
    try:
        url = "http://{0}:5001/api/spray_confirm".format(MAC_IP)
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            response.read()
        print("[Button] Reported manual button spray to Mac.")
    except Exception as e:
        print("[Button] Could not report manual button spray to Mac: {0}".format(e))

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
        pruned = prune_backlog('before sync')
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

        pruned += prune_backlog('after sync')
        print("[Sync] Done. uploaded={0}, failed={1}, pruned={2}".format(uploaded, failed, pruned))
    finally:
        sync_lock.release()

def trigger_spray(duration=None, rotation=None, roi=None, source='http'):
    if duration is None:
        duration = DEFAULT_SPRAY_DURATION
    try:
        duration = max(0.0, float(duration))
    except Exception:
        duration = DEFAULT_SPRAY_DURATION

    if not spray_lock.acquire(False):
        print("[Spray] Ignoring {0} trigger because a spray is already running.".format(source))
        return False

    try:
        print("Activating solenoid on GPIO {0} for {1}s from {2}... (rotation={3}, roi={4})".format(
            SOLENOID_PIN, duration, source, rotation, roi
        ))

        video_duration_seconds = max(1.0, duration + VIDEO_POST_ROLL_SECONDS)
        video_duration_ms = int(video_duration_seconds * 1000)
        video_started = threading.Event()
        video_thread = threading.Thread(target=record_video, args=(video_duration_ms, rotation, roi, video_started))
        video_thread.daemon = True
        video_thread.start()
        if not video_started.wait(5.0):
            print("[Video] Warning: recorder did not acquire camera lock before spray.")
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
        if source == 'button':
            report_manual_spray(duration)
        return True
    finally:
        spray_lock.release()

def button_pressed():
    print("[Button] Manual spray button pressed on GPIO {0}.".format(BUTTON_PIN))
    thread = threading.Thread(target=trigger_spray, kwargs={'source': 'button'})
    thread.daemon = True
    thread.start()

def button_monitor():
    if not GPIO:
        return

    active_value = GPIO.LOW if BUTTON_ACTIVE_LOW else GPIO.HIGH
    last_pressed = (GPIO.input(BUTTON_PIN) == active_value)
    last_triggered = 0.0

    if last_pressed:
        print("[Button] GPIO {0} is already active at startup; waiting for release before triggering.".format(BUTTON_PIN))

    while True:
        try:
            pressed = (GPIO.input(BUTTON_PIN) == active_value)
            now = time.time()
            if pressed and not last_pressed and now - last_triggered >= BUTTON_BOUNCE_SECONDS:
                last_triggered = now
                button_pressed()
            last_pressed = pressed
            time.sleep(BUTTON_POLL_SECONDS)
        except Exception as e:
            print("[Button] Error reading GPIO {0}: {1}".format(BUTTON_PIN, e))
            time.sleep(1.0)

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

            success = trigger_spray(duration=duration, rotation=rotation, roi=roi, source='http')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            if success:
                self.wfile.write(b'{"status":"success","message":"solenoid triggered"}')
            else:
                self.wfile.write(b'{"status":"busy","message":"spray already running"}')
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
        pull_mode = GPIO.PUD_UP if BUTTON_ACTIVE_LOW else GPIO.PUD_DOWN
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=pull_mode)
        monitor_thread = threading.Thread(target=button_monitor)
        monitor_thread.daemon = True
        monitor_thread.start()
        print("GPIO initialized successfully.")
        print("Manual spray button listening on GPIO {0} with internal {1}.".format(
            BUTTON_PIN,
            "pull-up" if BUTTON_ACTIVE_LOW else "pull-down"
        ))

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
