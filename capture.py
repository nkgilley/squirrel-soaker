#!/usr/bin/env python3
# capture.py
# Captures stills from the Pi Camera and sends them to the Mac AI model for real-time inference.
# Compatible with Python 3.4.2.

import os
import time
import datetime
import subprocess

# --- Configuration ---
CAPTURE_INTERVAL_SECONDS = 30
START_HOUR = 6    # 6:00 AM
END_HOUR = 20     # 8:00 PM
ROTATION = 0      # Rotation in degrees (0, 90, 180, 270)
ROI = "0.05,0.15,0.3,0.3" # Region of Interest (digital zoom)
VIDEO_ROI = "0.0,0.0,0.6,0.6" # Video Region of Interest (digital zoom)
WIDTH = 1280
HEIGHT = 960
OUTPUT_DIR = os.path.expanduser('~/squirrel_soaker/captures')
MAC_IP = '192.168.86.137' # IP address of the Mac running the Flask app

# Ensure output directory exists
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

def is_dst_eastern(dt):
    """
    Determines if a given UTC datetime is in US Daylight Saving Time (EDT).
    DST starts the second Sunday of March and ends the first Sunday of November.
    """
    try:
        march_1 = datetime.datetime(dt.year, 3, 1)
        w_march = march_1.weekday()
        first_sun_march = 1 + (6 - w_march) % 7
        second_sun_march = first_sun_march + 7
        dst_start = datetime.datetime(dt.year, 3, second_sun_march, 2, 0, 0)
        
        nov_1 = datetime.datetime(dt.year, 11, 1)
        w_nov = nov_1.weekday()
        first_sun_nov = 1 + (6 - w_nov) % 7
        dst_end = datetime.datetime(dt.year, 11, first_sun_nov, 2, 0, 0)
        
        utc_start = dst_start + datetime.timedelta(hours=5)
        utc_end = dst_end + datetime.timedelta(hours=4)
        
        return utc_start <= dt < utc_end
    except Exception:
        return 4 <= dt.month <= 10

def get_eastern_time():
    """Returns the current time in US Eastern Timezone."""
    utc_now = datetime.datetime.utcnow()
    offset = 4 if is_dst_eastern(utc_now) else 5
    return utc_now - datetime.timedelta(hours=offset)

def is_daylight(dt):
    """Checks if the hour is within the configured START_HOUR and END_HOUR."""
    return START_HOUR <= dt.hour < END_HOUR

CONFIDENCE_THRESHOLD = 0.70

def fetch_config_from_mac():
    global CAPTURE_INTERVAL_SECONDS, ROTATION, ROI, VIDEO_ROI, CONFIDENCE_THRESHOLD
    import urllib.request
    import json
    url = "http://{0}:5001/api/settings".format(MAC_IP)
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data.get('status') == 'success':
                settings = data.get('settings', {})
                if 'capture_interval' in settings:
                    CAPTURE_INTERVAL_SECONDS = int(settings['capture_interval'])
                if 'camera_rotation' in settings:
                    ROTATION = int(settings['camera_rotation'])
                if 'camera_roi' in settings:
                    ROI = str(settings['camera_roi']).strip()
                if 'video_roi' in settings:
                    VIDEO_ROI = str(settings['video_roi']).strip()
                if 'confidence_threshold' in settings:
                    CONFIDENCE_THRESHOLD = float(settings['confidence_threshold'])
                print("[Config] Dynamic settings updated: Interval={0}s, Rotation={1}°, ROI={2}, VideoROI={3}, Threshold={4:.2f}".format(
                    CAPTURE_INTERVAL_SECONDS, ROTATION, ROI, VIDEO_ROI, CONFIDENCE_THRESHOLD
                ))
    except Exception as e:
        print("[Config] Could not sync dynamic settings from Mac (offline): {0}".format(e))


def check_for_squirrel(filepath, is_test=False):
    """Sends the captured still to the Mac Flask app to run inference."""
    import urllib.request
    import json
    
    url = "http://{0}:5001/api/predict".format(MAC_IP)
    if is_test:
        url += "?test=true"
    print("[Inference] Sending {0} to Mac predict API...".format(os.path.basename(filepath)))
    
    try:
        with open(filepath, 'rb') as f:
            img_data = f.read()
            
        req = urllib.request.Request(
            url,
            data=img_data,
            headers={'Content-Type': 'image/jpeg'},
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=15) as response:
            res_data = response.read().decode('utf-8')
            res_json = json.loads(res_data)
            return res_json.get('is_squirrel', False), res_json.get('confidence', 0.0), res_json.get('spray_duration', 3.0)
    except Exception as e:
        print("[Inference] Mac server offline or unreachable: {0}".format(e))
        return False, 0.0, 3.0

def trigger_spray_locally(duration):
    """Tells the local trigger_server to spray water for the specified duration."""
    import urllib.request
    import urllib.parse
    try:
        encoded_roi = urllib.parse.quote(VIDEO_ROI) if VIDEO_ROI else ''
        url = 'http://localhost:8080/spray?duration={0}&rotation={1}&roi={2}'.format(duration, ROTATION, encoded_roi)
        req = urllib.request.Request(url, method='POST')
        with urllib.request.urlopen(req, timeout=25) as response:
            print("[Trigger] Spray triggered successfully with duration {0}s.".format(duration))
    except Exception as e:
        print("[Trigger] Error triggering spray: {0}".format(e))

def capture_image():
    """Captures an image using raspistill and runs prediction/spray logic."""
    fetch_config_from_mac()
    local_time = get_eastern_time()
    filename = "img_{0}.jpg".format(local_time.strftime("%Y%m%d_%H%M%S"))
    filepath = os.path.join(OUTPUT_DIR, filename)
    
    # Construct the raspistill command
    cmd = ["raspistill", "-w", str(WIDTH), "-h", str(HEIGHT), "-o", filepath, "-t", "1000"]
    if ROTATION in [90, 180, 270]:
        cmd.extend(["-rot", str(ROTATION)])
    if ROI:
        cmd.extend(["-roi", ROI])
        
    print("[{0}] Capturing: {1}".format(
        local_time.strftime("%Y-%m-%d %H:%M:%S"), filepath
    ))
    
    try:
        subprocess.check_call(cmd)
        
        # Run real-time classification
        is_squirrel, confidence, spray_duration = check_for_squirrel(filepath)
        
        if is_squirrel and confidence > CONFIDENCE_THRESHOLD:
            print("[Inference] SQUIRREL DETECTED! Confidence: {0:.1f}%. Triggering spray for {1}s! 💦".format(confidence * 100, spray_duration))
            trigger_spray_locally(spray_duration)
        else:
            print("[Inference] No squirrel detected (Class: Not Squirrel, Confidence: {0:.1f}%)".format(confidence * 100))
            
        # Clean up local still on the Pi if the upload succeeded (confidence > 0)
        # Keeps local copy on the Pi's disk if the Mac is offline (so sync_images.sh can get it later)
        if confidence > 0.0:
            os.remove(filepath)
            print("[Cleanup] Cleaned up local image from Pi disk.")
            
    except subprocess.CalledProcessError as e:
        print("Error capturing image: {0}".format(e))
    except Exception as e:
        print("Error processing captured image: {0}".format(e))

def main():
    print("Starting Squirrel Soaker 9001 capture & inference loop...")
    print("Interval: {0}s, Range: {1}:00 - {2}:00 Eastern".format(
        CAPTURE_INTERVAL_SECONDS, START_HOUR, END_HOUR
    ))
    
    while True:
        try:
            local_time = get_eastern_time()
            if is_daylight(local_time):
                capture_image()
                time.sleep(CAPTURE_INTERVAL_SECONDS)
            else:
                now = get_eastern_time()
                target = now.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += datetime.timedelta(days=1)
                
                seconds_to_wait = (target - now).total_seconds()
                print("[{0}] Nighttime. Sleeping for {1:.1f} hours until {2}...".format(
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    seconds_to_wait / 3600.0,
                    target.strftime("%Y-%m-%d %H:%M:%S")
                ))
                time.sleep(min(seconds_to_wait, 3600))
        except KeyboardInterrupt:
            print("\nStopping capture loop.")
            break
        except Exception as e:
            print("Unexpected error in main loop: {0}".format(e))
            time.sleep(10)

if __name__ == "__main__":
    main()
