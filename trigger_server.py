#!/usr/bin/env python3
# trigger_server.py
# Lightweight HTTP server running on the Raspberry Pi 3 to trigger the solenoid.
# Compatible with Python 3.4.2.

import os
import sys
import time
import datetime
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    import RPi.GPIO as GPIO
except ImportError:
    print("Warning: RPi.GPIO library not found. GPIO triggers will be simulated.")
    GPIO = None

# --- Configuration ---
PORT = 8080
SOLENOID_PIN = 17
SPRAY_DURATION_SECONDS = 3.0

# Add the current directory to sys.path to allow importing capture
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def record_video(duration_ms=5000, rotation=None, roi=None):
    """Records a video using raspivid in a background thread."""
    # Try to import capture to get local timezone time and defaults if parameters are None
    try:
        import capture
        local_time = capture.get_eastern_time()
        default_rot = getattr(capture, 'ROTATION', 0)
        default_roi = getattr(capture, 'ROI', None)
    except Exception as e:
        print("[Video] Warning: could not import capture config: {0}".format(e))
        local_time = datetime.datetime.now()
        default_rot = 0
        default_roi = None

    rot = rotation if rotation is not None else default_rot
    selected_roi = roi if roi is not None else default_roi

    filename = "vid_{0}.h264".format(local_time.strftime("%Y%m%d_%H%M%S"))
    captures_dir = os.path.expanduser('~/squirrel_soaker/captures')
    
    # Ensure captures directory exists
    if not os.path.exists(captures_dir):
        os.makedirs(captures_dir)
        
    filepath = os.path.join(captures_dir, filename)
    
    # Construct raspivid command
    cmd = ["raspivid", "-t", str(duration_ms), "-w", "1280", "-h", "720", "-o", filepath]
    if rot in [90, 180, 270]:
        cmd.extend(["-rot", str(rot)])
    if selected_roi:
        cmd.extend(["-roi", selected_roi])

    print("[Video] Recording {0}s video to {1}... (rotation={2}, roi={3})".format(duration_ms / 1000.0, filepath, rot, selected_roi))
    try:
        subprocess.check_call(cmd)
        print("[Video] Finished recording: {0}".format(filepath))
    except Exception as e:
        print("[Video] Error recording video: {0}".format(e))

class TriggerHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Override to log requests to standard output cleanly
        print("[Server] {0}".format(format % args))

    def do_POST(self):
        from urllib.parse import urlparse, parse_qs
        parsed_path = urlparse(self.path)
        if parsed_path.path == '/spray':
            duration = SPRAY_DURATION_SECONDS
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

            print("Activating solenoid on GPIO {0} for {1}s... (rotation: {2}, roi: {3})".format(
                SOLENOID_PIN, duration, rotation, roi
            ))
            
            # Start background video recording thread
            # Record for spray duration + 2.0s (min 5.0s)
            video_duration_ms = int(max(5.0, duration + 2.0) * 1000)
            video_thread = threading.Thread(target=record_video, args=(video_duration_ms, rotation, roi))
            video_thread.daemon = True
            video_thread.start()
            
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
        else:
            self.send_response(404)
            self.end_headers()

def run():
    # Setup GPIO if available
    if GPIO:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(SOLENOID_PIN, GPIO.OUT)
        GPIO.output(SOLENOID_PIN, GPIO.LOW)

    server_address = ('', PORT)
    httpd = HTTPServer(server_address, TriggerHandler)
    print("Solenoid trigger server listening on port {0}...".format(PORT))
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
