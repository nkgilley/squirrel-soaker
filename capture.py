#!/usr/bin/env python3
# capture.py
# Captures stills from the Pi Camera and sends them to the Mac AI model for inference.
# Compatible with Python 3.4.2.

import os
import time
import datetime
import subprocess
import json
import shutil
import math

ANALYSIS_INTERVAL_SECONDS = 5
SAVE_INTERVAL_SECONDS = 30
START_HOUR = 6
END_HOUR = 20
DAYLIGHT_MODE = "sun"
DAYLIGHT_LATITUDE = 38.9586
DAYLIGHT_LONGITUDE = -77.3570
SUNRISE_OFFSET_MINUTES = 0
SUNSET_OFFSET_MINUTES = 0
ROTATION = 0
ROI = "0.05,0.15,0.3,0.3"
VIDEO_ROI = "0.0,0.0,0.6,0.6"
WIDTH = 1280
HEIGHT = 960
ANALYSIS_WIDTH = 960
ANALYSIS_HEIGHT = 720
ANALYSIS_JPEG_QUALITY = 65
REVIEW_JPEG_QUALITY = 90
MOTION_PREFILTER_ENABLED = True
MOTION_THRESHOLD = 6.0
MOTION_FORCE_INTERVAL_SECONDS = 30
RASPISTILL_TIMEOUT_SECONDS = 20
OUTPUT_DIR = os.path.expanduser('~/squirrel_soaker/captures')
MAC_IP = '192.168.86.137'

CONFIDENCE_THRESHOLD = 0.70
last_review_save_time = 0.0
last_analysis_sent_time = 0.0
last_motion_fingerprint = None

def is_dst_eastern(dt):
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
    try:
        utc_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    except AttributeError:
        utc_now = datetime.datetime.utcnow()
    offset = 4 if is_dst_eastern(utc_now) else 5
    return utc_now - datetime.timedelta(hours=offset)

def get_eastern_utc_offset_hours(dt):
    return 4 if is_dst_eastern(dt) else 5

def normalize_degrees(value):
    return value % 360.0

def normalize_hours(value):
    return value % 24.0

def calculate_sun_time(local_date, latitude, longitude, is_sunrise):
    day_of_year = local_date.timetuple().tm_yday
    lng_hour = longitude / 15.0
    approx_hour = 6.0 if is_sunrise else 18.0
    t = day_of_year + ((approx_hour - lng_hour) / 24.0)

    mean_anomaly = (0.9856 * t) - 3.289
    true_longitude = normalize_degrees(
        mean_anomaly
        + (1.916 * math.sin(math.radians(mean_anomaly)))
        + (0.020 * math.sin(math.radians(2 * mean_anomaly)))
        + 282.634
    )

    right_ascension = math.degrees(math.atan(0.91764 * math.tan(math.radians(true_longitude))))
    right_ascension = normalize_degrees(right_ascension)
    longitude_quadrant = math.floor(true_longitude / 90.0) * 90.0
    ascension_quadrant = math.floor(right_ascension / 90.0) * 90.0
    right_ascension = (right_ascension + longitude_quadrant - ascension_quadrant) / 15.0

    sin_declination = 0.39782 * math.sin(math.radians(true_longitude))
    cos_declination = math.cos(math.asin(sin_declination))
    zenith = 90.833
    cos_hour_angle = (
        math.cos(math.radians(zenith))
        - (sin_declination * math.sin(math.radians(latitude)))
    ) / (cos_declination * math.cos(math.radians(latitude)))

    if cos_hour_angle > 1:
        return None
    if cos_hour_angle < -1:
        return None

    if is_sunrise:
        hour_angle = 360.0 - math.degrees(math.acos(cos_hour_angle))
    else:
        hour_angle = math.degrees(math.acos(cos_hour_angle))
    hour_angle /= 15.0

    local_mean_time = hour_angle + right_ascension - (0.06571 * t) - 6.622
    utc_hour = normalize_hours(local_mean_time - lng_hour)
    utc_midnight = datetime.datetime(local_date.year, local_date.month, local_date.day)
    utc_dt = utc_midnight + datetime.timedelta(hours=utc_hour)
    offset = get_eastern_utc_offset_hours(utc_dt)
    local_dt = utc_dt - datetime.timedelta(hours=offset)
    while local_dt.date() < local_date:
        local_dt += datetime.timedelta(days=1)
    while local_dt.date() > local_date:
        local_dt -= datetime.timedelta(days=1)
    return local_dt

def clamp_hour(value, default_value):
    try:
        return max(0, min(23, int(value)))
    except Exception:
        return default_value

def get_daylight_window(dt):
    if DAYLIGHT_MODE == "fixed":
        start_hour = clamp_hour(START_HOUR, 6)
        end_hour = clamp_hour(END_HOUR, 20)
        start = dt.replace(hour=start_hour, minute=0, second=0, microsecond=0)
        end = dt.replace(hour=end_hour, minute=0, second=0, microsecond=0)
        if end <= start:
            end += datetime.timedelta(days=1)
        return start, end, "fixed"

    sunrise = calculate_sun_time(dt.date(), DAYLIGHT_LATITUDE, DAYLIGHT_LONGITUDE, True)
    sunset = calculate_sun_time(dt.date(), DAYLIGHT_LATITUDE, DAYLIGHT_LONGITUDE, False)
    if sunrise is None or sunset is None:
        start = dt.replace(hour=clamp_hour(START_HOUR, 6), minute=0, second=0, microsecond=0)
        end = dt.replace(hour=clamp_hour(END_HOUR, 20), minute=0, second=0, microsecond=0)
        return start, end, "fixed-fallback"

    sunrise += datetime.timedelta(minutes=SUNRISE_OFFSET_MINUTES)
    sunset += datetime.timedelta(minutes=SUNSET_OFFSET_MINUTES)
    return sunrise, sunset, "sun"

def is_daylight(dt):
    start, end, _source = get_daylight_window(dt)
    return start <= dt < end

def get_next_daylight_start(dt):
    start, end, source = get_daylight_window(dt)
    if dt < start:
        return start, source
    tomorrow = dt + datetime.timedelta(days=1)
    next_start, _next_end, next_source = get_daylight_window(tomorrow)
    return next_start, next_source

def fetch_config_from_mac():
    global ANALYSIS_INTERVAL_SECONDS, SAVE_INTERVAL_SECONDS, ROTATION, ROI, VIDEO_ROI, CONFIDENCE_THRESHOLD
    global ANALYSIS_WIDTH, ANALYSIS_HEIGHT, ANALYSIS_JPEG_QUALITY, REVIEW_JPEG_QUALITY
    global MOTION_PREFILTER_ENABLED, MOTION_THRESHOLD, MOTION_FORCE_INTERVAL_SECONDS
    global START_HOUR, END_HOUR, DAYLIGHT_MODE, DAYLIGHT_LATITUDE, DAYLIGHT_LONGITUDE
    global SUNRISE_OFFSET_MINUTES, SUNSET_OFFSET_MINUTES
    import urllib.request

    url = "http://{0}:5001/api/settings".format(MAC_IP)
    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data.get('status') == 'success':
                settings = data.get('settings', {})
                if 'analysis_interval' in settings:
                    ANALYSIS_INTERVAL_SECONDS = int(settings['analysis_interval'])
                elif 'capture_interval' in settings:
                    ANALYSIS_INTERVAL_SECONDS = int(settings['capture_interval'])
                if 'save_interval' in settings:
                    SAVE_INTERVAL_SECONDS = int(settings['save_interval'])
                if 'camera_rotation' in settings:
                    ROTATION = int(settings['camera_rotation'])
                if 'camera_roi' in settings:
                    ROI = str(settings['camera_roi']).strip()
                if 'video_roi' in settings:
                    VIDEO_ROI = str(settings['video_roi']).strip()
                if 'confidence_threshold' in settings:
                    CONFIDENCE_THRESHOLD = float(settings['confidence_threshold'])
                if 'analysis_width' in settings:
                    ANALYSIS_WIDTH = int(settings['analysis_width'])
                if 'analysis_height' in settings:
                    ANALYSIS_HEIGHT = int(settings['analysis_height'])
                if 'analysis_jpeg_quality' in settings:
                    ANALYSIS_JPEG_QUALITY = int(settings['analysis_jpeg_quality'])
                if 'review_jpeg_quality' in settings:
                    REVIEW_JPEG_QUALITY = int(settings['review_jpeg_quality'])
                if 'motion_prefilter_enabled' in settings:
                    value = settings['motion_prefilter_enabled']
                    if isinstance(value, str):
                        MOTION_PREFILTER_ENABLED = value.strip().lower() in ('1', 'true', 'yes', 'on')
                    else:
                        MOTION_PREFILTER_ENABLED = bool(value)
                if 'motion_threshold' in settings:
                    MOTION_THRESHOLD = float(settings['motion_threshold'])
                if 'motion_force_interval' in settings:
                    MOTION_FORCE_INTERVAL_SECONDS = int(settings['motion_force_interval'])
                if 'daylight_mode' in settings:
                    mode = str(settings['daylight_mode']).strip().lower()
                    DAYLIGHT_MODE = mode if mode in ("sun", "fixed") else "sun"
                if 'daylight_latitude' in settings:
                    DAYLIGHT_LATITUDE = float(settings['daylight_latitude'])
                if 'daylight_longitude' in settings:
                    DAYLIGHT_LONGITUDE = float(settings['daylight_longitude'])
                if 'daylight_start_hour' in settings:
                    START_HOUR = clamp_hour(settings['daylight_start_hour'], START_HOUR)
                if 'daylight_end_hour' in settings:
                    END_HOUR = clamp_hour(settings['daylight_end_hour'], END_HOUR)
                if 'sunrise_offset_minutes' in settings:
                    SUNRISE_OFFSET_MINUTES = int(settings['sunrise_offset_minutes'])
                if 'sunset_offset_minutes' in settings:
                    SUNSET_OFFSET_MINUTES = int(settings['sunset_offset_minutes'])
                daylight_start, daylight_end, daylight_source = get_daylight_window(get_eastern_time())
                print("[Config] Dynamic settings updated: AnalysisInterval={0}s, SaveInterval={1}s, AnalysisSize={2}x{3} q{4}, ReviewSize={5}x{6} q{7}, Motion={8} threshold={9:.1f} force={10}s, Rotation={11}, ROI={12}, VideoROI={13}, Threshold={14:.2f}, Daylight={15} {16}-{17}".format(
                    ANALYSIS_INTERVAL_SECONDS, SAVE_INTERVAL_SECONDS,
                    ANALYSIS_WIDTH, ANALYSIS_HEIGHT, ANALYSIS_JPEG_QUALITY,
                    WIDTH, HEIGHT, REVIEW_JPEG_QUALITY,
                    MOTION_PREFILTER_ENABLED, MOTION_THRESHOLD, MOTION_FORCE_INTERVAL_SECONDS,
                    ROTATION, ROI, VIDEO_ROI, CONFIDENCE_THRESHOLD,
                    daylight_source,
                    daylight_start.strftime("%H:%M"),
                    daylight_end.strftime("%H:%M")
                ))
    except Exception as e:
        print("[Config] Could not sync dynamic settings from Mac: {0}".format(e))

def ensure_output_dir():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

def check_for_squirrel(filename, img_data, should_save=True, is_test=False):
    import urllib.request

    save_flag = '1' if should_save else '0'
    url = "http://{0}:5001/api/predict?save={1}".format(MAC_IP, save_flag)
    if is_test:
        url += "&test=true"
    print("[Inference] Sending {0} to Mac predict API from memory... (save={1})".format(filename, save_flag))

    started_at = time.time()
    try:
        req = urllib.request.Request(
            url,
            data=img_data,
            headers={'Content-Type': 'image/jpeg'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            res_json = json.loads(response.read().decode('utf-8'))
            res_json['_upload_ms'] = round((time.time() - started_at) * 1000, 1)
            return res_json
    except Exception as e:
        print("[Inference] Mac server offline or unreachable: {0}".format(e))
        return {
            'is_squirrel': False,
            'confidence': 0.0,
            'spray_duration': 3.0,
            '_upload_ms': round((time.time() - started_at) * 1000, 1),
            'error': str(e)
        }

def report_pi_status(status):
    import urllib.request

    try:
        url = "http://{0}:5001/api/pi_status".format(MAC_IP)
        payload = json.dumps(status).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=2) as response:
            response.read()
    except Exception as e:
        print("[Status] Could not report Pi status: {0}".format(e))

def get_motion_score(img_data):
    global last_motion_fingerprint
    try:
        from io import BytesIO
        from PIL import Image
        img = Image.open(BytesIO(img_data)).convert('L').resize((32, 24))
        pixels = list(img.getdata())
        if last_motion_fingerprint is None:
            last_motion_fingerprint = pixels
            return None
        diff = sum(abs(a - b) for a, b in zip(pixels, last_motion_fingerprint)) / float(len(pixels))
        last_motion_fingerprint = pixels
        return diff
    except Exception as e:
        print("[Motion] Could not compute motion score: {0}".format(e))
        return None

def capture_jpeg_bytes(cmd):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout_data, stderr_data = proc.communicate(timeout=RASPISTILL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise

    if proc.returncode != 0:
        err = stderr_data.decode('utf-8', errors='ignore') if stderr_data else ''
        raise subprocess.CalledProcessError(proc.returncode, cmd, output=err)
    return stdout_data

def write_backlog_image(filename, img_data):
    ensure_output_dir()
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, 'wb') as f:
        f.write(img_data)
    print("[Backlog] Wrote {0} to Pi SD because Mac did not accept saved frame.".format(filepath))
    return filepath

def find_camera_still_command():
    for binary in ('rpicam-still', 'libcamera-still', 'raspistill'):
        path = shutil.which(binary)
        if path:
            return binary
    return 'raspistill'

def build_still_command(width, height, jpeg_quality):
    camera_cmd = find_camera_still_command()
    if camera_cmd in ('rpicam-still', 'libcamera-still'):
        cmd = [
            camera_cmd,
            "--width", str(width),
            "--height", str(height),
            "--quality", str(jpeg_quality),
            "--output", "-",
            "--timeout", "1000",
            "--nopreview",
            "--immediate",
            "--encoding", "jpg"
        ]
        if ROTATION in [0, 180]:
            cmd.extend(["--rotation", str(ROTATION)])
        elif ROTATION in [90, 270]:
            print("[Camera] Warning: {0} only supports rotation 0 or 180; ignoring rotation {1}.".format(camera_cmd, ROTATION))
        if ROI:
            cmd.extend(["--roi", ROI])
        return cmd

    cmd = [
        camera_cmd,
        "-w", str(width),
        "-h", str(height),
        "-q", str(jpeg_quality),
        "-o", "-",
        "-t", "1000"
    ]
    if ROTATION in [90, 180, 270]:
        cmd.extend(["-rot", str(ROTATION)])
    if ROI:
        cmd.extend(["-roi", ROI])
    return cmd

def trigger_spray_locally(duration):
    import urllib.request
    import urllib.parse

    try:
        encoded_roi = urllib.parse.quote(VIDEO_ROI) if VIDEO_ROI else ''
        url = 'http://localhost:8080/spray?duration={0}&rotation={1}&roi={2}'.format(duration, ROTATION, encoded_roi)
        req = urllib.request.Request(url, method='POST')
        with urllib.request.urlopen(req, timeout=25):
            print("[Trigger] Spray triggered successfully with duration {0}s.".format(duration))
    except Exception as e:
        print("[Trigger] Error triggering spray: {0}".format(e))

def capture_image():
    global last_review_save_time, last_analysis_sent_time

    loop_started_at = time.time()
    fetch_started_at = time.time()
    fetch_config_from_mac()
    config_ms = (time.time() - fetch_started_at) * 1000
    local_time = get_eastern_time()
    now_seconds = time.time()
    should_save = (last_review_save_time <= 0.0) or (now_seconds - last_review_save_time >= SAVE_INTERVAL_SECONDS)
    filename = "img_{0}.jpg".format(local_time.strftime("%Y%m%d_%H%M%S"))

    capture_width = WIDTH if should_save else ANALYSIS_WIDTH
    capture_height = HEIGHT if should_save else ANALYSIS_HEIGHT
    jpeg_quality = REVIEW_JPEG_QUALITY if should_save else ANALYSIS_JPEG_QUALITY

    cmd = build_still_command(capture_width, capture_height, jpeg_quality)

    print("[{0}] Capturing to memory: {1} ({2}x{3} q{4}, save={5})".format(
        local_time.strftime("%Y-%m-%d %H:%M:%S"),
        filename,
        capture_width,
        capture_height,
        jpeg_quality,
        1 if should_save else 0
    ))

    try:
        capture_started_at = time.time()
        img_data = capture_jpeg_bytes(cmd)
        capture_ms = (time.time() - capture_started_at) * 1000
        file_bytes = len(img_data)
        motion_started_at = time.time()
        motion_score = get_motion_score(img_data)
        motion_ms = (time.time() - motion_started_at) * 1000
        force_analysis = (last_analysis_sent_time <= 0.0) or (now_seconds - last_analysis_sent_time >= MOTION_FORCE_INTERVAL_SECONDS)
        motion_allowed = (
            should_save or
            not MOTION_PREFILTER_ENABLED or
            force_analysis or
            motion_score is None or
            motion_score >= MOTION_THRESHOLD
        )

        if not motion_allowed:
            print("[Motion] Skipping inference. score={0:.2f}, threshold={1:.2f}".format(motion_score, MOTION_THRESHOLD))
            report_pi_status({
                'captured_at': local_time.strftime("%Y-%m-%d %H:%M:%S"),
                'status': 'motion_skipped',
                'filename': filename,
                'should_save': should_save,
                'sd_write': False,
                'file_bytes': file_bytes,
                'motion_score': motion_score,
                'motion_threshold': MOTION_THRESHOLD,
                'config_ms': round(config_ms, 1),
                'capture_ms': round(capture_ms, 1),
                'motion_ms': round(motion_ms, 1),
                'total_ms': round((time.time() - loop_started_at) * 1000, 1),
                'analysis_interval': ANALYSIS_INTERVAL_SECONDS,
                'save_interval': SAVE_INTERVAL_SECONDS
            })
            return

        result = check_for_squirrel(filename, img_data, should_save=should_save)
        last_analysis_sent_time = now_seconds
        is_squirrel = result.get('detected_squirrel', result.get('is_squirrel', False))
        should_spray = result.get('should_spray', result.get('is_squirrel', False))
        confidence = result.get('confidence', 0.0)
        spray_duration = result.get('spray_duration', 3.0)
        if should_spray:
            print("[Inference] SQUIRREL CONFIRMED! Confidence: {0:.1f}%. Triggering spray for {1}s.".format(confidence * 100, spray_duration))
            trigger_spray_locally(spray_duration)
        else:
            decision = result.get('spray_decision', {})
            if is_squirrel and decision:
                print("[Inference] Squirrel detected, waiting for decision gate: {0}/{1} hits, avg {2:.1f}%.".format(
                    decision.get('hits', 0),
                    decision.get('required_hits', 1),
                    float(decision.get('average_confidence', 0.0)) * 100
                ))
            else:
                print("[Inference] No squirrel detected. Confidence: {0:.1f}%".format(confidence * 100))

        if confidence > 0.0:
            if should_save:
                last_review_save_time = now_seconds
            print("[Cleanup] No local image cleanup needed; frame stayed in memory.")
        elif should_save:
            write_backlog_image(filename, img_data)
        else:
            print("[Cleanup] Dropped unsaved transient frame from memory after failed prediction.")
        report_pi_status({
            'captured_at': local_time.strftime("%Y-%m-%d %H:%M:%S"),
            'status': 'analyzed',
            'filename': filename,
            'should_save': should_save,
            'saved_for_review': should_save and confidence > 0.0,
            'sd_write': should_save and confidence <= 0.0,
            'file_bytes': file_bytes,
            'width': capture_width,
            'height': capture_height,
            'jpeg_quality': jpeg_quality,
            'motion_score': motion_score,
            'motion_threshold': MOTION_THRESHOLD,
            'is_squirrel': is_squirrel,
            'confidence': confidence,
            'config_ms': round(config_ms, 1),
            'capture_ms': round(capture_ms, 1),
            'motion_ms': round(motion_ms, 1),
            'upload_ms': result.get('_upload_ms'),
            'server_metrics': result.get('metrics', {}),
            'total_ms': round((time.time() - loop_started_at) * 1000, 1),
            'analysis_interval': ANALYSIS_INTERVAL_SECONDS,
            'save_interval': SAVE_INTERVAL_SECONDS
        })
    except subprocess.TimeoutExpired:
        print("Error capturing image: camera command timed out after {0}s".format(RASPISTILL_TIMEOUT_SECONDS))
    except subprocess.CalledProcessError as e:
        print("Error capturing image: {0}".format(e))
    except Exception as e:
        print("Error processing captured image: {0}".format(e))

def main():
    print("Starting Squirrel Soaker 9001 capture & inference loop...")
    print("Analysis interval: {0}s, save interval: {1}s, daylight mode: {2}".format(
        ANALYSIS_INTERVAL_SECONDS, SAVE_INTERVAL_SECONDS, DAYLIGHT_MODE
    ))

    while True:
        try:
            fetch_config_from_mac()
            local_time = get_eastern_time()
            daylight_start, daylight_end, daylight_source = get_daylight_window(local_time)
            if is_daylight(local_time):
                started_at = time.time()
                capture_image()
                elapsed = time.time() - started_at
                time.sleep(max(0.0, ANALYSIS_INTERVAL_SECONDS - elapsed))
            else:
                now = get_eastern_time()
                target, target_source = get_next_daylight_start(now)
                seconds_to_wait = (target - now).total_seconds()
                print("[{0}] Nighttime ({1}: {2}-{3}). Sleeping for {4:.1f} hours until {5} ({6})...".format(
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                    daylight_source,
                    daylight_start.strftime("%H:%M"),
                    daylight_end.strftime("%H:%M"),
                    seconds_to_wait / 3600.0,
                    target.strftime("%Y-%m-%d %H:%M:%S"),
                    target_source
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
