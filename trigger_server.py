#!/usr/bin/env python3
# trigger_server.py
# HTTP server running on the Raspberry Pi to trigger the solenoid and sync backlog files.

import os
import sys
import time
import datetime
import subprocess
import threading
import shutil
import fcntl
import json
import platform
from http.server import BaseHTTPRequestHandler, HTTPServer

GPIO_BACKEND = None
GPIO = None
solenoid_device = None
button_device = None

try:
    from gpiozero import Button, DigitalOutputDevice
    GPIO_BACKEND = 'gpiozero'
except ImportError:
    Button = None
    DigitalOutputDevice = None
    try:
        import RPi.GPIO as GPIO
        GPIO_BACKEND = 'rpigpio'
    except ImportError:
        print("Warning: no GPIO library found. GPIO triggers will be simulated.")

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
VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
sync_lock = threading.Lock()
spray_lock = threading.Lock()
latest_settings = {}

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def get_local_time_and_defaults():
    global latest_settings
    local_time = datetime.datetime.now()
    default_rot = 0
    default_roi = None

    try:
        import capture
        local_time = capture.get_eastern_time()
        default_rot = getattr(capture, 'ROTATION', default_rot)
        default_roi = getattr(capture, 'VIDEO_ROI', getattr(capture, 'ROI', None))
    except Exception as e:
        print("[Video] Warning: could not import capture config: {0}".format(e))

    try:
        import urllib.request
        url = "http://{0}:5001/api/settings".format(MAC_IP)
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode('utf-8'))
        if data.get('status') == 'success':
            settings = data.get('settings', {})
            latest_settings = settings
            if 'camera_rotation' in settings:
                default_rot = int(settings['camera_rotation'])
            if 'video_rotation' in settings:
                default_rot = int(settings['video_rotation'])
            if 'video_roi' in settings:
                default_roi = str(settings['video_roi']).strip()
    except Exception as e:
        print("[Video] Warning: could not fetch video settings from Mac: {0}".format(e))

    return local_time, default_rot, default_roi

def get_camera_tuning():
    if not latest_settings:
        get_local_time_and_defaults()
    return {
        'camera_awb': str(latest_settings.get('camera_awb', 'auto') or 'auto'),
        'camera_exposure': str(latest_settings.get('camera_exposure', 'normal') or 'normal'),
        'camera_metering': str(latest_settings.get('camera_metering', 'centre') or 'centre'),
        'camera_saturation': float(latest_settings.get('camera_saturation', 1.0)),
        'camera_contrast': float(latest_settings.get('camera_contrast', 1.0)),
        'camera_sharpness': float(latest_settings.get('camera_sharpness', 1.0)),
        'camera_tuning_enabled': bool(latest_settings.get('camera_tuning_enabled', False))
    }

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def get_backlog_files():
    files = []
    if not os.path.exists(CAPTURES_DIR):
        return files
    for filename in os.listdir(CAPTURES_DIR):
        lower = filename.lower()
        if not (lower.endswith('.jpg') or lower.endswith('.jpeg') or lower.endswith('.h264') or lower.endswith('.mp4')):
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

def bytes_to_mb(value):
    try:
        return round(float(value) / (1024.0 * 1024.0), 1)
    except Exception:
        return None

def read_first_line(path):
    try:
        with open(path, 'r') as f:
            return f.readline().strip()
    except Exception:
        return None

def get_backlog_summary():
    files = get_backlog_files()
    total_bytes = sum(info.get('size', 0) for info in files)
    return {
        'backlog_files': len(files),
        'backlog_bytes': total_bytes,
        'backlog_mb': bytes_to_mb(total_bytes)
    }

def get_system_health_snapshot():
    snapshot = {
        'hostname': platform.node(),
        'python_version': platform.python_version(),
        'gpio_backend': GPIO_BACKEND or 'none',
        'solenoid_pin': SOLENOID_PIN,
        'button_pin': BUTTON_PIN,
        'button_active_low': BUTTON_ACTIVE_LOW,
        'button_pressed': is_button_pressed() if gpio_available() else None,
        'video_tmp_dir': VIDEO_TMP_DIR,
        'captures_dir': CAPTURES_DIR,
        'camera_video_command': find_camera_video_command(),
        'camera_still_command': find_camera_still_command()
    }
    temp_raw = read_first_line('/sys/class/thermal/thermal_zone0/temp')
    if temp_raw:
        try:
            snapshot['cpu_temp_c'] = round(float(temp_raw) / 1000.0, 1)
        except Exception:
            pass
    try:
        load_1m, load_5m, load_15m = os.getloadavg()
        snapshot['load_1m'] = round(load_1m, 2)
        snapshot['load_5m'] = round(load_5m, 2)
        snapshot['load_15m'] = round(load_15m, 2)
    except Exception:
        pass
    for key, path in (('disk', '/'), ('shm', '/dev/shm')):
        try:
            usage = shutil.disk_usage(path)
            snapshot['{0}_free_mb'.format(key)] = bytes_to_mb(usage.free)
            snapshot['{0}_used_percent'.format(key)] = round((usage.used / float(usage.total)) * 100.0, 1)
        except Exception:
            pass
    try:
        meminfo = {}
        with open('/proc/meminfo', 'r') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(':')] = int(parts[1])
        if 'MemTotal' in meminfo:
            snapshot['mem_total_mb'] = round(meminfo['MemTotal'] / 1024.0, 1)
        if 'MemAvailable' in meminfo:
            snapshot['mem_available_mb'] = round(meminfo['MemAvailable'] / 1024.0, 1)
    except Exception:
        pass
    try:
        proc = subprocess.Popen(['vcgencmd', 'get_throttled'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_data, _stderr_data = proc.communicate(timeout=2)
        if proc.returncode == 0:
            raw = stdout_data.decode('utf-8', errors='ignore').strip()
            snapshot['throttled_raw'] = raw
            value = int(raw.split('=')[-1], 16)
            snapshot['throttled_now'] = bool(value & 0x1 or value & 0x2 or value & 0x4 or value & 0x8)
            snapshot['throttled_ever'] = bool(value & 0x10000 or value & 0x20000 or value & 0x40000 or value & 0x80000)
    except Exception:
        pass
    snapshot.update(get_backlog_summary())
    warnings = []
    if snapshot.get('backlog_files', 0) > 0:
        warnings.append('sd_backlog_present')
    if snapshot.get('disk_used_percent', 0) >= 85:
        warnings.append('sd_card_high_usage')
    if snapshot.get('cpu_temp_c', 0) >= 75:
        warnings.append('cpu_hot')
    if snapshot.get('throttled_now'):
        warnings.append('pi_throttled')
    snapshot['warnings'] = warnings
    return snapshot

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

def find_camera_still_command():
    for binary in ('rpicam-still', 'libcamera-still', 'raspistill'):
        path = shutil.which(binary)
        if path:
            return binary
    return 'raspistill'

def append_rpicam_tuning(cmd):
    tuning = get_camera_tuning()
    if not tuning.get('camera_tuning_enabled'):
        return
    cmd.extend(["--awb", tuning['camera_awb']])
    cmd.extend(["--exposure", tuning['camera_exposure']])
    cmd.extend(["--metering", tuning['camera_metering']])
    cmd.extend(["--saturation", str(tuning['camera_saturation'])])
    cmd.extend(["--contrast", str(tuning['camera_contrast'])])
    cmd.extend(["--sharpness", str(tuning['camera_sharpness'])])

def build_video_command(duration_ms, filepath, rotation=None, roi=None):
    camera_cmd = find_camera_video_command()
    if camera_cmd in ('rpicam-vid', 'libcamera-vid'):
        cmd = [
            camera_cmd,
            "--timeout", str(duration_ms),
            "--width", str(VIDEO_WIDTH),
            "--height", str(VIDEO_HEIGHT),
            "--output", filepath,
            "--codec", "libav",
            "--libav-format", "mp4",
            "--libav-video-codec", "libx264",
            "--nopreview"
        ]
        if rotation in [0, 180]:
            cmd.extend(["--rotation", str(rotation)])
        elif rotation in [90, 270]:
            print("[Video] Warning: {0} only supports rotation 0 or 180; ignoring rotation {1}.".format(camera_cmd, rotation))
        if roi:
            cmd.extend(["--roi", roi])
        append_rpicam_tuning(cmd)
        return cmd

    cmd = ["raspivid", "-t", str(duration_ms), "-w", str(VIDEO_WIDTH), "-h", str(VIDEO_HEIGHT), "-o", filepath]
    if rotation in [90, 180, 270]:
        cmd.extend(["-rot", str(rotation)])
    if roi:
        cmd.extend(["-roi", roi])
    return cmd

def record_video(duration_ms=5000, rotation=None, roi=None, started_event=None, upload=True, name_prefix='vid'):
    import urllib.parse

    local_time, default_rot, default_roi = get_local_time_and_defaults()
    rot = rotation if rotation is not None else default_rot
    selected_roi = roi if roi is not None else default_roi

    ensure_dir(VIDEO_TMP_DIR)

    filename = "{0}_{1}.mp4".format(name_prefix, local_time.strftime("%Y%m%d_%H%M%S"))
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

        if not upload:
            file_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
            return {'status': 'success', 'filename': filename, 'bytes': file_size, 'uploaded': False}

        encoded = urllib.parse.quote(filename)
        url = "http://{0}:5001/api/upload_video?filename={1}".format(MAC_IP, encoded)
        try:
            post_file(url, filepath, 'video/mp4', timeout=30)
            if os.path.exists(filepath):
                os.remove(filepath)
            print("[Video] Uploaded {0} from RAM and removed local copy.".format(filename))
            return {'status': 'success', 'filename': filename, 'uploaded': True}
        except Exception as e:
            ensure_dir(CAPTURES_DIR)
            prune_backlog('before saving a failed video upload')
            backlog_path = os.path.join(CAPTURES_DIR, filename)
            shutil.move(filepath, backlog_path)
            print("[Video] Upload failed ({0}); saved video to SD backlog: {1}".format(e, backlog_path))
            prune_backlog('after saving a failed video upload')
            return {'status': 'backlogged', 'filename': filename, 'path': backlog_path, 'error': str(e)}
    except subprocess.TimeoutExpired:
        print("[Video] Error recording video: camera command timed out after {0}s".format(timeout_seconds))
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
        return {'status': 'error', 'message': 'camera command timed out'}
    except Exception as e:
        if started_event:
            started_event.set()
        print("[Video] Error recording video: {0}".format(e))
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
        return {'status': 'error', 'message': str(e)}

def build_still_command(filepath, rotation=None, roi=None):
    camera_cmd = find_camera_still_command()
    if camera_cmd in ('rpicam-still', 'libcamera-still'):
        cmd = [
            camera_cmd,
            "--width", "1280",
            "--height", "960",
            "--quality", "80",
            "--output", filepath,
            "--timeout", "1000",
            "--nopreview",
            "--immediate",
            "--encoding", "jpg"
        ]
        if rotation in [0, 180]:
            cmd.extend(["--rotation", str(rotation)])
        if roi:
            cmd.extend(["--roi", roi])
        append_rpicam_tuning(cmd)
        return cmd
    cmd = [camera_cmd, "-w", "1280", "-h", "960", "-q", "80", "-o", filepath, "-t", "1000"]
    if rotation in [90, 180, 270]:
        cmd.extend(["-rot", str(rotation)])
    if roi:
        cmd.extend(["-roi", roi])
    return cmd

def test_camera_capture():
    local_time, rot, roi = get_local_time_and_defaults()
    ensure_dir(VIDEO_TMP_DIR)
    filepath = os.path.join(VIDEO_TMP_DIR, "camera_test_{0}.jpg".format(local_time.strftime("%Y%m%d_%H%M%S")))
    cmd = build_still_command(filepath, rotation=rot, roi=roi)
    lock_file = open(CAMERA_LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        subprocess.check_call(cmd, timeout=15)
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        finally:
            lock_file.close()
    size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
    try:
        os.remove(filepath)
    except Exception:
        pass
    return {'status': 'success', 'bytes': size, 'rotation': rot, 'roi': roi}

def run_benchmark(iterations=3):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pi_benchmark.py')
    if not os.path.exists(script):
        return {'status': 'error', 'message': 'pi_benchmark.py is not deployed'}
    proc = subprocess.Popen(
        [sys.executable, script, '--json', '--iterations', str(iterations)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout_data, stderr_data = proc.communicate(timeout=90)
    stdout_text = stdout_data.decode('utf-8', errors='ignore')
    stderr_text = stderr_data.decode('utf-8', errors='ignore')
    try:
        parsed = json.loads(stdout_text)
    except Exception:
        parsed = {'status': 'error', 'message': 'benchmark returned non-json output', 'stdout': stdout_text[-1000:]}
    parsed['returncode'] = proc.returncode
    if stderr_text:
        parsed['stderr_tail'] = stderr_text[-1000:]
    return parsed

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
                elif lower.endswith('.h264') or lower.endswith('.mp4'):
                    encoded = urllib.parse.quote(filename)
                    url = "http://{0}:5001/api/upload_video?filename={1}".format(MAC_IP, encoded)
                    content_type = 'video/mp4' if lower.endswith('.mp4') else 'video/h264'
                    post_file(url, filepath, content_type, timeout=30)
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

        if gpio_available():
            try:
                set_solenoid(True)
                time.sleep(duration)
            finally:
                set_solenoid(False)
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
    if not gpio_available():
        return

    last_pressed = is_button_pressed()
    last_triggered = 0.0

    if last_pressed:
        print("[Button] GPIO {0} is already active at startup; waiting for release before triggering.".format(BUTTON_PIN))

    while True:
        try:
            pressed = is_button_pressed()
            now = time.time()
            if pressed and not last_pressed and now - last_triggered >= BUTTON_BOUNCE_SECONDS:
                last_triggered = now
                button_pressed()
            last_pressed = pressed
            time.sleep(BUTTON_POLL_SECONDS)
        except Exception as e:
            print("[Button] Error reading GPIO {0}: {1}".format(BUTTON_PIN, e))
            time.sleep(1.0)

def gpio_available():
    return GPIO_BACKEND in ('gpiozero', 'rpigpio')

def setup_gpio():
    global solenoid_device, button_device

    if GPIO_BACKEND == 'gpiozero':
        solenoid_device = DigitalOutputDevice(SOLENOID_PIN, active_high=True, initial_value=False)
        button_device = Button(BUTTON_PIN, pull_up=BUTTON_ACTIVE_LOW, bounce_time=BUTTON_BOUNCE_SECONDS)
        return True

    if GPIO_BACKEND == 'rpigpio':
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(SOLENOID_PIN, GPIO.OUT)
        GPIO.output(SOLENOID_PIN, GPIO.LOW)
        pull_mode = GPIO.PUD_UP if BUTTON_ACTIVE_LOW else GPIO.PUD_DOWN
        GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=pull_mode)
        return True

    return False

def set_solenoid(active):
    if GPIO_BACKEND == 'gpiozero' and solenoid_device:
        if active:
            solenoid_device.on()
        else:
            solenoid_device.off()
    elif GPIO_BACKEND == 'rpigpio' and GPIO:
        GPIO.output(SOLENOID_PIN, GPIO.HIGH if active else GPIO.LOW)

def is_button_pressed():
    if GPIO_BACKEND == 'gpiozero' and button_device:
        return bool(button_device.is_pressed)
    if GPIO_BACKEND == 'rpigpio' and GPIO:
        active_value = GPIO.LOW if BUTTON_ACTIVE_LOW else GPIO.HIGH
        return GPIO.input(BUTTON_PIN) == active_value
    return False

def cleanup_gpio():
    if GPIO_BACKEND == 'gpiozero':
        if solenoid_device:
            solenoid_device.off()
            solenoid_device.close()
        if button_device:
            button_device.close()
    elif GPIO_BACKEND == 'rpigpio' and GPIO:
        GPIO.cleanup()

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
        elif parsed_path.path == '/test_camera':
            try:
                self.send_json({'status': 'success', 'result': test_camera_capture()})
            except Exception as e:
                self.send_json({'status': 'error', 'message': str(e)}, 500)
        elif parsed_path.path == '/test_video':
            query = parse_qs(parsed_path.query)
            duration = 1.0
            if 'duration' in query:
                try:
                    duration = max(0.5, min(float(query['duration'][0]), 5.0))
                except ValueError:
                    pass
            result = record_video(
                duration_ms=int(duration * 1000),
                upload=True,
                name_prefix='vid_test'
            )
            code = 200 if result.get('status') in ('success', 'backlogged') else 500
            self.send_json({'status': result.get('status', 'unknown'), 'result': result}, code)
        elif parsed_path.path == '/test_relay':
            query = parse_qs(parsed_path.query)
            confirmed = query.get('confirm', ['false'])[0].lower() in ('1', 'true', 'yes', 'on')
            if not confirmed:
                self.send_json({'status': 'error', 'message': 'confirm=true is required for relay tests'}, 400)
                return
            duration = 0.2
            if 'duration' in query:
                try:
                    duration = max(0.05, min(float(query['duration'][0]), 1.0))
                except ValueError:
                    pass
            try:
                if gpio_available():
                    set_solenoid(True)
                    time.sleep(duration)
                    set_solenoid(False)
                else:
                    time.sleep(duration)
                self.send_json({'status': 'success', 'message': 'relay pulsed', 'duration': duration})
            except Exception as e:
                try:
                    set_solenoid(False)
                except Exception:
                    pass
                self.send_json({'status': 'error', 'message': str(e)}, 500)
        elif parsed_path.path == '/benchmark':
            query = parse_qs(parsed_path.query)
            iterations = 3
            if 'iterations' in query:
                try:
                    iterations = max(1, min(int(query['iterations'][0]), 10))
                except ValueError:
                    pass
            try:
                result = run_benchmark(iterations=iterations)
                code = 200 if result.get('status') != 'error' else 500
                self.send_json({'status': result.get('status', 'success'), 'result': result}, code)
            except Exception as e:
                self.send_json({'status': 'error', 'message': str(e)}, 500)
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        from urllib.parse import urlparse
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/diagnostics':
            self.send_json({'status': 'success', 'diagnostics': get_system_health_snapshot()})
        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, payload, status_code=200):
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode('utf-8'))

def run():
    if setup_gpio():
        monitor_thread = threading.Thread(target=button_monitor)
        monitor_thread.daemon = True
        monitor_thread.start()
        print("GPIO initialized successfully using {0}.".format(GPIO_BACKEND))
        print("Manual spray button listening on GPIO {0} with internal {1}.".format(
            BUTTON_PIN,
            "pull-up" if BUTTON_ACTIVE_LOW else "pull-down"
        ))
    else:
        print("GPIO unavailable; trigger server is running in simulation mode.")

    server_address = ('', PORT)
    httpd = HTTPServer(server_address, TriggerHandler)
    print("Solenoid trigger server listening on port {}...".format(PORT))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping trigger server.")
    finally:
        if gpio_available():
            cleanup_gpio()
            print("GPIO cleaned up.")

if __name__ == '__main__':
    run()
