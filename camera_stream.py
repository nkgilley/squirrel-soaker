#!/usr/bin/env python3
# camera_stream.py
# High-performance HTTP MJPEG camera streamer for Raspberry Pi 3.
# Dynamically syncs rotation and ROI/zoom from the Mac settings.
# Compatible with Python 3.4.2+.

import io
import time
import logging
import threading
import socketserver
from http import server

try:
    import picamera
except ImportError:
    picamera = None
    print("Warning: picamera library not found. Camera will be simulated.")

# --- Configuration ---
PORT = 8554
MAC_IP = '192.168.86.137'  # IP of the Mac running the Flask app
WIDTH = 1920
HEIGHT = 1080
FPS = 5
BITRATE = 25000000

PAGE = """\
<html>
<head>
<title>Squirrel Soaker Live Feed</title>
</head>
<body>
<center><h1>Squirrel Soaker Live Feed</h1></center>
<center><img src="stream.mjpg" width="1920" height="1080" style="max-width: 100%; height: auto;"></center>
</body>
</html>
"""

class StreamingOutput(object):
    def __init__(self):
        self.frame = None
        self.buffer = io.BytesIO()
        self.condition = threading.Condition()

    def write(self, buf):
        if buf.startswith(b'\xff\xd8'):
            self.buffer.truncate()
            with self.condition:
                self.frame = self.buffer.getvalue()
                self.condition.notify_all()
            self.buffer.seek(0)
        return self.buffer.write(buf)

class StreamingHandler(server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress verbose frame access logging
        pass

    def do_GET(self):
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            content = PAGE.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=FRAME')
            self.end_headers()
            try:
                while True:
                    with output.condition:
                        output.condition.wait()
                        frame = output.frame
                    self.wfile.write(b'--FRAME\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(frame))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.info(
                    'Removed streaming client %s: %s',
                    self.client_address, str(e))
        else:
            self.send_response(404)
            self.end_headers()

class StreamingServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

# Global output buffer
output = StreamingOutput()
camera = None

def sync_settings_loop():
    global camera
    if not picamera or not camera:
        return
        
    import urllib.request
    import json
    
    url = "http://{}:5001/api/settings".format(MAC_IP)
    print("[Config Sync] Settings sync thread started. Querying URL: {}".format(url))
    
    current_rot = 0
    current_roi = ""
    
    while True:
        try:
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data.get('status') == 'success':
                    settings = data.get('settings', {})
                    
                    # 1. Update Rotation
                    rot = int(settings.get('camera_rotation', 0))
                    if rot != current_rot:
                        camera.rotation = rot
                        current_rot = rot
                        print("[Config Sync] Camera rotation updated to: {}°".format(rot))
                        
                    # 2. Update ROI/Zoom
                    roi_str = settings.get('camera_roi', '').strip()
                    if roi_str != current_roi:
                        current_roi = roi_str
                        if roi_str:
                            try:
                                parts = [float(p.strip()) for p in roi_str.split(',')]
                                if len(parts) == 4:
                                    camera.zoom = (parts[0], parts[1], parts[2], parts[3])
                                    print("[Config Sync] Camera zoom/ROI updated to: {}".format(camera.zoom))
                            except Exception as ze:
                                print("[Config Sync] Error parsing camera_roi: {}".format(ze))
                        else:
                            camera.zoom = (0.0, 0.0, 1.0, 1.0)
                            print("[Config Sync] Camera zoom/ROI reset to full screen")
        except Exception as e:
            # Silent fallback if Mac is temporarily offline
            pass
        time.sleep(10.0)

def main():
    global camera
    logging.basicConfig(level=logging.INFO)
    
    if picamera:
        camera = picamera.PiCamera(resolution='{}x{}'.format(WIDTH, HEIGHT), framerate=FPS)
        camera.rotation = 0
        camera.zoom = (0.0, 0.0, 1.0, 1.0)
        camera.start_recording(output, format='mjpeg', bitrate=BITRATE)
        print("[Camera] Pi camera started recording in MJPEG format at {}x{}, {}fps, {}bps.".format(WIDTH, HEIGHT, FPS, BITRATE))
        
        # Start background settings sync thread
        sync_thread = threading.Thread(target=sync_settings_loop)
        sync_thread.daemon = True
        sync_thread.start()
    else:
        print("[Camera] Simulation mode. No physical camera output will be generated.")

    try:
        address = ('', PORT)
        server = StreamingServer(address, StreamingHandler)
        print("[Server] Streaming server listening on port {}...".format(PORT))
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping streaming server.")
    finally:
        if picamera and camera:
            camera.stop_recording()
            camera.close()
            print("[Camera] Camera stopped.")

if __name__ == '__main__':
    main()
