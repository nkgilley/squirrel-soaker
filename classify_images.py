import os
import time
import shutil
import subprocess
from flask import Flask, jsonify, request, send_from_directory, render_template_string
import threading

app = Flask(__name__)

# --- Classifier Logging ---
log_lock = threading.Lock()
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'classifier.log')

def log_message(msg):
    # Print to stdout/stderr so normal console logging works
    print(msg)
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
    formatted_msg = "[{0}] {1}\n".format(timestamp, msg)
    try:
        with log_lock:
            with open(LOG_FILE, 'a') as f:
                f.write(formatted_msg)
    except Exception as e:
        print("Failed to write to classifier.log: {0}".format(e))

def upload_to_0x0(filepath):
    import urllib.request
    import uuid
    
    if not filepath or not os.path.exists(filepath):
        return None
        
    boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
    filename = os.path.basename(filepath)
    
    try:
        with open(filepath, 'rb') as f:
            file_content = f.read()
            
        # Construct multipart form body
        body = (
            '--{0}\r\n'
            'Content-Disposition: form-data; name="file"; filename="{1}"\r\n'
            'Content-Type: video/mp4\r\n\r\n'
        ).format(boundary, filename).encode('utf-8')
        
        body += file_content
        body += '\r\n--{0}--\r\n'.format(boundary).encode('utf-8')
        
        headers = {
            'Content-Type': 'multipart/form-data; boundary={0}'.format(boundary),
            'Content-Length': str(len(body))
        }
        
        req = urllib.request.Request('https://0x0.st', data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=20) as res:
            url = res.read().decode('utf-8').strip()
            log_message("[Upload] Synced video uploaded to 0x0.st: {0}".format(url))
            return url
    except Exception as e:
        log_message("[Upload] Error uploading video to 0x0.st: {0}".format(e))
        return None

# --- ML Model Configuration ---
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model.pth')
model = None
model_classes = []
device = None

def load_trained_model():
    global model, model_classes, device
    if os.path.exists(MODEL_PATH):
        try:
            import torch
            import torch.nn as nn
            from torchvision import models
            
            # Select device
            if torch.backends.mps.is_available():
                device = torch.device("mps")
            elif torch.cuda.is_available():
                device = torch.device("cuda")
            else:
                device = torch.device("cpu")
                
            checkpoint = torch.load(MODEL_PATH, map_location=device)
            model_classes = checkpoint['classes']
            
            # Recreate ResNet-18 model structure
            model = models.resnet18()
            num_ftrs = model.fc.in_features
            model.fc = nn.Linear(num_ftrs, len(model_classes))
            
            # Load weights
            model.load_state_dict(checkpoint['model_state_dict'])
            model = model.to(device)
            model.eval()
            log_message("Successfully loaded trained model with classes: {0}".format(model_classes))
        except Exception as e:
            log_message("Error loading model: {0}".format(e))
            model = None

def model_predict(filepath):
    global model, model_classes, device
    if model is None:
        load_trained_model()
        if model is None:
            return False, 0.0
            
    try:
        import torch
        from PIL import Image
        from torchvision import transforms
        
        # Open and preprocess image
        img = Image.open(filepath).convert('RGB')
        transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])
        input_tensor = transform(img).unsqueeze(0).to(device)
        
        with torch.no_grad():
            outputs = model(input_tensor)
            probabilities = torch.softmax(outputs, dim=1)[0]
            confidence, class_idx = torch.max(probabilities, 0)
            
            class_name = model_classes[class_idx.item()]
            is_squirrel = (class_name == 'squirrel')
            return is_squirrel, confidence.item()
    except Exception as e:
        log_message("Error during prediction: {0}".format(e))
        return False, 0.0

# Initial model load attempt
load_trained_model()

# --- Configuration ---
PI_IP = '192.168.86.136'

# --- Directory Paths ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def load_env_file():
    env_path = os.path.join(BASE_DIR, '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        os.environ[key.strip()] = val.strip()
        except Exception as e:
            print("Error loading env file:", e)

load_env_file()

RAW_DIR = os.path.join(BASE_DIR, 'data', 'raw')
DATASET_DIR = os.path.join(BASE_DIR, 'data', 'dataset')
SQUIRREL_DIR = os.path.join(DATASET_DIR, 'squirrel')
NOT_SQUIRREL_DIR = os.path.join(DATASET_DIR, 'not_squirrel')
VIDEOS_DIR = os.path.join(BASE_DIR, 'data', 'videos')
TRASH_DIR = os.path.join(BASE_DIR, 'data', 'trash')

AUTOMATION_STATUS_FILE = os.path.join(BASE_DIR, 'data', 'automation_status.json')
SETTINGS_FILE = os.path.join(BASE_DIR, 'data', 'settings.json')

default_settings = {
    'capture_interval': 15,
    'gemini_api_key': os.environ.get('GEMINI_API_KEY', ''),
    'camera_rotation': 0,
    'camera_roi': '0.05,0.15,0.3,0.3',
    'video_roi': '0.0,0.0,0.6,0.6',
    'confidence_threshold': 0.70,
    'spray_cooldown_seconds': 60,
    'notification_type': 'join',
    'email_smtp_server': '192.169.86.113:25',
    'email_to': '',
    'join_api_key': os.environ.get('JOIN_API_KEY', ''),
    'spray_duration': 3.0,
    'long_spray_duration': 5.0,
    'long_spray_threshold_hours': 2.0,
    'retention_days_raw': 3,
    'retention_days_not_squirrel': 7,
    'retention_min_not_squirrel': 1000,
    'retention_days_trash': 1,
    'retention_days_videos': 14
}


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                import json
                settings = json.load(f)
                merged = default_settings.copy()
                merged.update(settings)
                return merged
        except Exception as e:
            print("Error loading settings:", e)
    return default_settings.copy()

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            import json
            json.dump(settings, f, indent=2)
    except Exception as e:
        print("Error saving settings:", e)
BLASTS_LOG_FILE = os.path.join(BASE_DIR, 'data', 'blasts_log.json')
last_spray_time = 0.0

def send_blast_notification(blast_type, confidence=None):
    # Wait for the video to be recorded on the Pi (takes max(5s, duration + 2s)).
    # We wait 8 seconds to be safe and let it finish.
    time.sleep(8)
    
    # Run sync_images.sh to pull the video and process it
    try:
        script_path = os.path.join(BASE_DIR, 'sync_images.sh')
        subprocess.run([script_path], capture_output=True, text=True, check=True)
        process_synced_videos()
    except Exception as e:
        log_message("[Notification] Error syncing during notification: {0}".format(e))

    # Find the latest video in data/videos/ to attach/link
    video_path = None
    video_filename = None
    try:
        import glob
        video_files = glob.glob(os.path.join(VIDEOS_DIR, '*.mp4'))
        if video_files:
            video_files.sort(key=os.path.getmtime)
            candidate_path = video_files[-1]
            # Verify the video file was modified within the last 45 seconds to be sure it's from this spray
            if time.time() - os.path.getmtime(candidate_path) < 45:
                video_path = candidate_path
                video_filename = os.path.basename(video_path)
    except Exception as e:
        log_message("[Notification] Error finding latest video: {0}".format(e))

    settings = load_settings()
    notification_type = settings.get('notification_type', 'join')
    
    title = "🐿️ Squirrel Blasted! 💦"
    if blast_type == 'auto':
        msg = "Automatic repeller triggered a water spray! (Model confidence: {0:.1f}%)".format(confidence * 100 if confidence else 0)
    else:
        msg = "Manual spray triggered from the web interface."
        
    log_message(msg)
    
    # Construct video URL for Join Push (Try 0x0.st first, fallback to local IP)
    video_url = None
    if video_path and video_filename:
        video_url = upload_to_0x0(video_path)
        if not video_url:
            import socket
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(('10.255.255.255', 1))
                local_ip = s.getsockname()[0]
                s.close()
            except Exception:
                local_ip = '127.0.0.1'
            video_url = "http://{0}:5001/video/{1}".format(local_ip, video_filename)
        
    # Send Join Push
    if notification_type in ['join', 'both']:
        api_key = settings.get('join_api_key')
        if api_key:
            import urllib.request
            import urllib.parse
            try:
                params = {
                    'apikey': api_key,
                    'title': title,
                    'text': msg,
                    'deviceId': 'group.all'
                }
                if video_url:
                    params['url'] = video_url
                    params['file'] = video_url
                url = "https://joinjoaomgcd.appspot.com/_ah/api/messaging/v1/sendPush?" + urllib.parse.urlencode(params)
                req = urllib.request.Request(url, method='GET')
                with urllib.request.urlopen(req, timeout=5) as response:
                    response.read()
                log_message("[Notification] Join push sent successfully.")
            except Exception as e:
                log_message("[Notification] Error sending Join push: {0}".format(e))

    # Send Email
    if notification_type in ['email', 'both']:
        smtp_server = settings.get('email_smtp_server', '192.169.86.113:25')
        to_email = settings.get('email_to')
        if smtp_server and to_email:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            from email.mime.base import MIMEBase
            from email import encoders
            try:
                host = '192.169.86.113'
                port = 25
                if ':' in smtp_server:
                    parts = smtp_server.split(':')
                    host = parts[0]
                    port = int(parts[1])
                else:
                    host = smtp_server
                
                mime_msg = MIMEMultipart()
                mime_msg['Subject'] = title
                mime_msg['From'] = 'squirrel-sentry@localhost'
                mime_msg['To'] = to_email
                
                # Attach text message
                mime_msg.attach(MIMEText(msg, 'plain'))
                
                # Attach video file if available
                if video_path and os.path.exists(video_path):
                    try:
                        with open(video_path, 'rb') as attachment:
                            part = MIMEBase('application', 'octet-stream')
                            part.set_payload(attachment.read())
                            encoders.encode_base64(part)
                            part.add_header(
                                'Content-Disposition',
                                'attachment; filename= {0}'.format(video_filename),
                            )
                            mime_msg.attach(part)
                    except Exception as ve:
                        log_message("[Notification] Error attaching video: {0}".format(ve))
                
                with smtplib.SMTP(host, port, timeout=10) as server:
                    server.send_message(mime_msg)
                log_message("[Notification] Local SMTP email sent successfully.")
            except Exception as e:
                log_message("[Notification] Error sending SMTP email: {0}".format(e))

def log_blast(blast_type, confidence=None):
    import json
    import datetime
    
    blast_entry = {
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'type': blast_type,
        'confidence': confidence
    }
    
    blasts = []
    if os.path.exists(BLASTS_LOG_FILE):
        try:
            with open(BLASTS_LOG_FILE, 'r') as f:
                blasts = json.load(f)
                if not isinstance(blasts, list):
                    blasts = []
        except Exception as e:
            print("Error reading blasts log:", e)
            
    blasts.append(blast_entry)
    
    try:
        with open(BLASTS_LOG_FILE, 'w') as f:
            json.dump(blasts, f, indent=2)
        # Asynchronous notification dispatch
        import threading
        threading.Thread(target=send_blast_notification, args=(blast_type, confidence)).start()
    except Exception as e:
        print("Error writing to blasts log:", e)

def get_current_spray_duration():
    settings = load_settings()
    std_duration = settings.get('spray_duration', 3.0)
    long_duration = settings.get('long_spray_duration', 5.0)
    threshold_hours = settings.get('long_spray_threshold_hours', 2.0)
    
    blasts = []
    if os.path.exists(BLASTS_LOG_FILE):
        try:
            with open(BLASTS_LOG_FILE, 'r') as f:
                import json
                blasts = json.load(f)
                if not isinstance(blasts, list):
                    blasts = []
        except Exception:
            pass
            
    if not blasts:
        return long_duration
        
    try:
        import datetime
        last_timestamp_str = blasts[-1].get('timestamp')
        last_dt = datetime.datetime.strptime(last_timestamp_str, "%Y-%m-%d %H:%M:%S")
        time_diff = datetime.datetime.now() - last_dt
        diff_hours = time_diff.total_seconds() / 3600.0
        
        if diff_hours >= threshold_hours:
            return long_duration
        else:
            return std_duration
    except Exception as e:
        print("Error calculating spray duration:", e)
        return std_duration

automation_enabled = True
training_process = None

last_exit_code = None
model_reloaded = False

def load_automation_status():
    global automation_enabled
    if os.path.exists(AUTOMATION_STATUS_FILE):
        try:
            with open(AUTOMATION_STATUS_FILE, 'r') as f:
                import json
                automation_enabled = json.load(f).get('enabled', True)
        except Exception as e:
            print("Error loading automation status:", e)

def save_automation_status(enabled):
    try:
        with open(AUTOMATION_STATUS_FILE, 'w') as f:
            import json
            json.dump({'enabled': enabled}, f)
    except Exception as e:
        print("Error saving automation status:", e)

# Initial load of status
load_automation_status()

def clean_directory_by_age(directory, retention_days):
    if not os.path.exists(directory):
        return 0
    deleted = 0
    now = time.time()
    cutoff = now - (retention_days * 86400)
    for f in os.listdir(directory):
        fp = os.path.join(directory, f)
        if os.path.isfile(fp):
            try:
                mtime = os.path.getmtime(fp)
                if mtime < cutoff:
                    os.remove(fp)
                    deleted += 1
            except Exception as e:
                print("Error deleting file {0}: {1}".format(fp, e))
    return deleted

def clean_videos_directory(directory, retention_days):
    if not os.path.exists(directory):
        return 0
    import json
    import datetime
    
    # Load blasts log to identify favorited items
    blasts = []
    if os.path.exists(BLASTS_LOG_FILE):
        try:
            with open(BLASTS_LOG_FILE, 'r') as f:
                blasts = json.load(f)
                if not isinstance(blasts, list):
                    blasts = []
        except Exception as e:
            print("Error reading blasts log for cleanup:", e)
            
    deleted = 0
    now = time.time()
    cutoff = now - (retention_days * 86400)
    for f in os.listdir(directory):
        fp = os.path.join(directory, f)
        if os.path.isfile(fp):
            base_name = os.path.splitext(f)[0]
            is_fav = False
            video_time = get_video_timestamp(base_name + '.mp4')
            if video_time:
                for entry in blasts:
                    if not entry.get('favorite'):
                        continue
                    entry_time_str = entry.get('timestamp')
                    if not entry_time_str:
                        continue
                    try:
                        entry_time = datetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
                        if abs((video_time - entry_time).total_seconds()) < 6.0:
                            is_fav = True
                            break
                    except Exception:
                        continue
            
            if is_fav:
                continue
                
            try:
                mtime = os.path.getmtime(fp)
                if mtime < cutoff:
                    os.remove(fp)
                    deleted += 1
            except Exception as e:
                print("Error deleting file {0}: {1}".format(fp, e))
    return deleted

def clean_not_squirrel_directory(not_squirrel_dir, retention_days, min_count):
    if not os.path.exists(not_squirrel_dir):
        return 0
        
    files = []
    for f in os.listdir(not_squirrel_dir):
        fp = os.path.join(not_squirrel_dir, f)
        if os.path.isfile(fp):
            try:
                files.append((fp, os.path.getmtime(fp)))
            except Exception as e:
                print("Error statting file {0}: {1}".format(fp, e))
                
    # Sort files newest first
    files.sort(key=lambda x: x[1], reverse=True)
    
    now = time.time()
    cutoff = now - (retention_days * 86400)
    deleted = 0
    
    for idx, (fp, mtime) in enumerate(files):
        # Always keep the newest files up to min_count
        if idx < min_count:
            continue
        # Delete if older than retention_days
        if mtime < cutoff:
            try:
                os.remove(fp)
                deleted += 1
            except Exception as e:
                print("Error deleting old not_squirrel image {0}: {1}".format(fp, e))
    return deleted

def run_storage_cleanup():
    while True:
        try:
            settings = load_settings()
            
            raw_days = settings.get('retention_days_raw', 3.0)
            ns_days = settings.get('retention_days_not_squirrel', 7.0)
            ns_min = settings.get('retention_min_not_squirrel', 1000)
            trash_days = settings.get('retention_days_trash', 1.0)
            vid_days = settings.get('retention_days_videos', 14.0)
            
            log_message("[Storage Cleanup] Running automated cleanup...")
            
            del_raw = clean_directory_by_age(RAW_DIR, raw_days)
            del_trash = clean_directory_by_age(TRASH_DIR, trash_days)
            del_vid = clean_videos_directory(VIDEOS_DIR, vid_days)
            del_ns = clean_not_squirrel_directory(NOT_SQUIRREL_DIR, ns_days, ns_min)
            
            if del_raw > 0 or del_trash > 0 or del_vid > 0 or del_ns > 0:
                log_message("[Storage Cleanup] Done. Deleted: raw={0}, trash={1}, videos={2}, not_squirrel={3}".format(
                    del_raw, del_trash, del_vid, del_ns
                ))
            else:
                log_message("[Storage Cleanup] Done. No files needed pruning.")
                
        except Exception as e:
            log_message("Error in storage cleanup loop: {0}".format(e))
            
        time.sleep(3600)

# Ensure directories exist
for d in [RAW_DIR, SQUIRREL_DIR, NOT_SQUIRREL_DIR, VIDEOS_DIR, TRASH_DIR]:
    os.makedirs(d, exist_ok=True)

# Keep track of classification history for undo (in-memory stack)
classification_history = []

def get_stats():
    """Returns the count of images in each directory."""
    raw_files = [f for f in os.listdir(RAW_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    squirrel_files = [f for f in os.listdir(SQUIRREL_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    not_squirrel_files = [f for f in os.listdir(NOT_SQUIRREL_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    
    latest_mtime = 0
    for d in [RAW_DIR, SQUIRREL_DIR, NOT_SQUIRREL_DIR]:
        if os.path.exists(d):
            for f in os.listdir(d):
                if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                    try:
                        mtime = os.path.getmtime(os.path.join(d, f))
                        if mtime > latest_mtime:
                            latest_mtime = mtime
                    except Exception:
                        pass
                        
    import datetime
    current_hour = datetime.datetime.now().hour

    return {
        'raw_count': len(raw_files),
        'squirrel_count': len(squirrel_files),
        'not_squirrel_count': len(not_squirrel_files),
        'total_dataset_count': len(squirrel_files) + len(not_squirrel_files),
        'latest_image_mtime': latest_mtime,
        'current_hour': current_hour
    }


def get_next_raw_image():
    """Returns the filename of the next raw image to classify."""
    raw_files = sorted([f for f in os.listdir(RAW_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
    if raw_files:
        return raw_files[0]
    return None

def process_synced_videos():
    """Finds all .h264 files in RAW_DIR, converts them to .mp4 in VIDEOS_DIR, generates thumbnails, and deletes source files."""
    if not os.path.exists(VIDEOS_DIR):
        os.makedirs(VIDEOS_DIR, exist_ok=True)
        
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        print("Warning: ffmpeg not found. Cannot convert .h264 videos to .mp4.")
        return
        
    for filename in os.listdir(RAW_DIR):
        if filename.lower().endswith('.h264'):
            h264_path = os.path.join(RAW_DIR, filename)
            mp4_filename = os.path.splitext(filename)[0] + '.mp4'
            mp4_path = os.path.join(VIDEOS_DIR, mp4_filename)
            
            # Wrap H.264 into an MP4 container instantly using ffmpeg (-c:v copy)
            cmd = [ffmpeg_path, '-y', '-i', h264_path, '-c:v', 'copy', mp4_path]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                os.remove(h264_path)
                print("Successfully converted {0} to {1}".format(filename, mp4_filename))
                
                # Generate thumbnail
                thumb_filename = os.path.splitext(mp4_filename)[0] + '.jpg'
                thumb_path = os.path.join(VIDEOS_DIR, thumb_filename)
                thumb_cmd = [ffmpeg_path, '-y', '-i', mp4_path, '-ss', '00:00:00.5', '-vframes', '1', thumb_path]
                subprocess.run(thumb_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                print("Generated thumbnail for {0}".format(mp4_filename))
            except Exception as e:
                print("Error processing video {0}: {1}".format(filename, str(e)))
                
    # Generate missing thumbnails for existing mp4 files
    for filename in os.listdir(VIDEOS_DIR):
        if filename.lower().endswith('.mp4'):
            mp4_path = os.path.join(VIDEOS_DIR, filename)
            thumb_filename = os.path.splitext(filename)[0] + '.jpg'
            thumb_path = os.path.join(VIDEOS_DIR, thumb_filename)
            if not os.path.exists(thumb_path):
                thumb_cmd = [ffmpeg_path, '-y', '-i', mp4_path, '-ss', '00:00:00.5', '-vframes', '1', thumb_path]
                try:
                    subprocess.run(thumb_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    print("Generated missing thumbnail for {0}".format(filename))
                except Exception as e:
                    print("Error generating missing thumbnail for {0}: {1}".format(filename, e))

def get_video_timestamp(filename):
    """Parses vid_YYYYMMDD_HHMMSS.mp4 to a datetime object."""
    import re
    import datetime
    match = re.match(r'vid_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.mp4', filename)
    if match:
        parts = [int(p) for p in match.groups()]
        try:
            return datetime.datetime(parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])
        except Exception:
            return None
    return None

# Initial conversion on startup
process_synced_videos()


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Squirrel Soaker 9001 Classifier</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🐿️</text></svg>">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(22, 30, 49, 0.7);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            
            --color-squirrel: #10b981;
            --color-not-squirrel: #ef4444;
            --color-delete: #f59e0b;
            --color-sync: #3b82f6;
            --color-accuracy: #a855f7;
            
            --shadow-glow: 0 0 20px rgba(59, 130, 246, 0.15);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            user-select: none;
        }

        body {
            font-family: 'Outfit', sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            overflow-x: hidden;
            background-image: 
                radial-gradient(at 0% 0%, rgba(16, 185, 129, 0.05) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(239, 68, 68, 0.05) 0px, transparent 50%),
                radial-gradient(at 50% 50%, rgba(59, 130, 246, 0.03) 0px, transparent 80%);
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1.5rem 2rem;
            border-bottom: 1px solid var(--border-color);
            backdrop-filter: blur(12px);
            background: rgba(11, 15, 25, 0.5);
            position: sticky;
            top: 0;
            z-index: 10;
        }

        .logo-section h1 {
            font-size: 1.5rem;
            font-weight: 800;
            background: linear-gradient(135deg, #60a5fa 0%, #10b981 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }

        .logo-section p {
            font-size: 0.75rem;
            color: var(--text-secondary);
            margin-top: 2px;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .btn {
            font-family: 'Outfit', sans-serif;
            font-weight: 600;
            border: none;
            border-radius: 12px;
            padding: 0.75rem 1.25rem;
            cursor: pointer;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.9rem;
        }

        .btn-sync {
            background-color: var(--color-sync);
            color: white;
            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
        }

        .btn-sync:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(59, 130, 246, 0.4), var(--shadow-glow);
        }

        .btn-sync:active {
            transform: translateY(0);
        }

        .btn-sync:disabled {
            background-color: #334155;
            color: var(--text-secondary);
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }

        .container {
            display: grid;
            grid-template-columns: 280px 1fr;
            gap: 2rem;
            padding: 2rem;
            flex-grow: 1;
            max-width: 1400px;
            margin: 0 auto;
            width: 100%;
        }

        .sidebar {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 1.5rem;
            backdrop-filter: blur(16px);
        }

        .card-title {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: var(--text-secondary);
            margin-bottom: 1rem;
            font-weight: 600;
        }

        .stats-list {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .stat-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 1rem;
        }

        .stat-val {
            font-size: 1.25rem;
            font-weight: 600;
        }

        .stat-val.raw { color: #f8fafc; }
        .stat-val.squirrel { color: var(--color-squirrel); }
        .stat-val.not-squirrel { color: var(--color-not-squirrel); }

        .shortcuts-list {
            list-style: none;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            font-size: 0.85rem;
            color: var(--text-secondary);
        }

        .shortcut-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .key-badge {
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            padding: 0.2rem 0.5rem;
            font-family: monospace;
            color: var(--text-primary);
            font-weight: 600;
            min-width: 24px;
            text-align: center;
        }

        .main-workspace {
            display: flex;
            flex-direction: column;
            gap: 1.5rem;
            align-items: center;
            width: 100%;
        }

        .viewer-card {
            width: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            padding: 2rem;
            min-height: 500px;
        }

        .image-container {
            width: 100%;
            max-width: 800px;
            height: 480px;
            border-radius: 12px;
            overflow: hidden;
            background: #020617;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid var(--border-color);
            position: relative;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        }

        .image-container img {
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
            transition: transform 0.2s ease;
        }

        .image-container.flash-squirrel {
            box-shadow: 0 0 30px var(--color-squirrel);
            border-color: var(--color-squirrel);
        }

        .image-container.flash-not-squirrel {
            box-shadow: 0 0 30px var(--color-not-squirrel);
            border-color: var(--color-not-squirrel);
        }

        .image-container.flash-delete {
            box-shadow: 0 0 30px var(--color-delete);
            border-color: var(--color-delete);
        }

        .image-filename {
            margin-top: 1rem;
            font-size: 0.9rem;
            color: var(--text-secondary);
            font-family: monospace;
        }

        .action-buttons {
            display: flex;
            gap: 1.5rem;
            margin-top: 2rem;
            width: 100%;
            max-width: 600px;
        }

        .action-buttons .btn {
            flex: 1;
            justify-content: center;
            padding: 1rem;
            font-size: 1rem;
        }

        .btn-not-squirrel {
            background-color: transparent;
            border: 2px solid var(--color-not-squirrel);
            color: var(--color-not-squirrel);
        }

        .btn-not-squirrel:hover {
            background-color: var(--color-not-squirrel);
            color: white;
            box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3);
            transform: translateY(-2px);
        }

        .btn-delete {
            background-color: transparent;
            border: 2px solid var(--color-delete);
            color: var(--color-delete);
        }

        .btn-delete:hover {
            background-color: var(--color-delete);
            color: white;
            box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
            transform: translateY(-2px);
        }

        .btn-squirrel {
            background-color: var(--color-squirrel);
            color: white;
            box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3);
        }

        .btn-squirrel:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 16px rgba(16, 185, 129, 0.4);
        }

        .no-images {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            color: var(--text-secondary);
            height: 350px;
            gap: 1rem;
        }

        .no-images-icon {
            font-size: 4rem;
        }

        .spinner {
            border: 3px solid rgba(255, 255, 255, 0.1);
            width: 18px;
            height: 18px;
            border-radius: 50%;
            border-left-color: white;
            animation: spin 1s linear infinite;
            display: none;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        @keyframes pulse {
            0% { opacity: 0.4; }
            50% { opacity: 1; }
            100% { opacity: 0.4; }
        }

        .syncing .spinner {
            display: inline-block;
        }

        /* --- Paginated Grid Gallery CSS --- */
        .grid-gallery {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
            gap: 1.5rem;
            width: 100%;
            margin-top: 1rem;
        }

        .gallery-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            cursor: pointer;
        }

        .gallery-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
            border-color: rgba(255, 255, 255, 0.15);
        }

        .gallery-card img {
            width: 100%;
            height: 150px;
            object-fit: cover;
            border-bottom: 1px solid var(--border-color);
        }

        .card-actions-overlay {
            position: absolute;
            top: 8px;
            right: 8px;
            display: flex;
            gap: 6px;
            opacity: 0;
            transition: opacity 0.2s ease;
            z-index: 5;
        }

        .gallery-card:hover .card-actions-overlay {
            opacity: 1;
        }

        .action-icon-btn {
            background: rgba(11, 15, 25, 0.85);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            font-size: 0.95rem;
            transition: all 0.15s ease;
        }

        .action-icon-btn:hover {
            background: var(--color-sync);
            transform: scale(1.1);
        }

        .action-icon-btn.btn-delete-quick:hover {
            background: var(--color-delete);
        }

        .gallery-card-info {
            padding: 0.75rem;
            font-size: 0.8rem;
            color: var(--text-secondary);
            font-family: monospace;
            text-overflow: ellipsis;
            overflow: hidden;
            white-space: nowrap;
            text-align: center;
        }

        .pagination-container {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 0.5rem;
            margin-top: 2rem;
            width: 100%;
            flex-wrap: wrap;
        }

        .page-link {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.5rem 0.8rem;
            cursor: pointer;
            color: var(--text-primary);
            font-size: 0.85rem;
            transition: all 0.2s ease;
            font-weight: 600;
        }

        .page-link:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: var(--text-secondary);
        }

        .page-link.active {
            background: var(--color-sync);
            border-color: var(--color-sync);
            color: white;
        }

        .page-link:disabled {
            opacity: 0.4;
            cursor: not-allowed;
        }

        /* --- Dashboard CSS --- */
        .dash-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1.25rem;
            width: 100%;
            margin-bottom: 1.5rem;
        }
        .dash-card {
            background: rgba(15, 23, 42, 0.45);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.25rem;
            display: flex;
            flex-direction: column;
            gap: 0.4rem;
            position: relative;
            overflow: hidden;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.1);
        }
        .dash-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
        }
        .dash-card.blasted::before { background-color: var(--color-squirrel); }
        .dash-card.status::before { background-color: var(--color-sync); }
        .dash-card.queue::before { background-color: var(--color-delete); }
        .dash-card.accuracy::before { background-color: var(--color-accuracy); }

        .dash-card-val {
            font-size: 2.25rem;
            font-weight: 800;
            color: var(--text-primary);
            line-height: 1.1;
        }
        .dash-card-label {
            font-size: 0.8rem;
            color: var(--text-secondary);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.8px;
        }
        .dash-card-sub {
            font-size: 0.75rem;
            color: var(--text-secondary);
        }
        .dash-row {
            display: grid;
            grid-template-columns: 1.6fr 1fr;
            gap: 1.5rem;
            width: 100%;
        }
        .dash-panel {
            background: rgba(15, 23, 42, 0.3);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.2);
            min-height: 350px;
        }
        .dash-panel-title {
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 1.25rem;
            color: var(--text-primary);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .dash-feed-container {
            width: 100%;
            height: 240px;
            background: #020617;
            border-radius: 12px;
            overflow: hidden;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 1px solid var(--border-color);
            position: relative;
            box-shadow: inset 0 0 20px rgba(0,0,0,0.8);
        }
        .dash-feed-container img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        .dash-feed-overlay {
            position: absolute;
            top: 12px;
            left: 12px;
            background: rgba(15, 23, 42, 0.85);
            border: 1px solid var(--border-color);
            padding: 0.3rem 0.7rem;
            border-radius: 20px;
            font-size: 0.7rem;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 0.4rem;
            backdrop-filter: blur(4px);
        }
        .dash-feed-overlay .dot {
            width: 7px;
            height: 7px;
            background-color: var(--color-not-squirrel);
            border-radius: 50%;
            display: inline-block;
            box-shadow: 0 0 8px var(--color-not-squirrel);
            animation: pulse 1.5s infinite;
        }
        @media (max-width: 900px) {
            .dash-grid {
                grid-template-columns: 1fr;
                gap: 1rem;
            }
            .dash-row {
                grid-template-columns: 1fr;
                gap: 1.5rem;
            }
        }

        /* --- Modal CSS --- */
        .modal {
            position: fixed;
            top: 0;
            left: 0;
            width: 100vw;
            height: 100vh;
            background: rgba(2, 6, 23, 0.9);
            backdrop-filter: blur(8px);
            z-index: 100;
            display: none;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: opacity 0.25s ease;
        }

        .modal.show {
            display: flex;
            opacity: 1;
        }

        .modal-content {
            position: relative;
            background: rgba(15, 23, 42, 0.95);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 2rem;
            max-width: 900px;
            width: 90%;
            display: flex;
            flex-direction: column;
            align-items: center;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
        }

        .close-btn {
            position: absolute;
            top: 1rem;
            right: 1.5rem;
            font-size: 2rem;
            cursor: pointer;
            color: var(--text-secondary);
            transition: color 0.15s ease;
        }

        .close-btn:hover {
            color: white;
        }

        @media (max-width: 768px) {
            .container {
                grid-template-columns: 1fr;
                padding: 1rem;
                gap: 1.5rem;
            }
            .sidebar {
                order: 2;
            }
            .main-workspace {
                order: 1;
            }
            .image-container {
                height: 320px;
            }
            header {
                padding: 1rem;
                flex-direction: column;
                gap: 1rem;
                text-align: center;
            }
            .header-actions {
                width: 100%;
                justify-content: center;
                flex-wrap: wrap;
            }
        }

        @media (max-width: 480px) {
            .action-buttons {
                gap: 0.5rem;
                margin-top: 1rem;
            }
            .action-buttons .btn {
                padding: 0.75rem 0.5rem;
                font-size: 0.85rem;
            }
            .viewer-card {
                padding: 1rem;
            }
        }
    </style>
</head>
<body>

    <header>
        <div class="logo-section">
            <h1>🐿️ Squirrel Soaker 9001</h1>
            <p>Dataset Image Classifier</p>
        </div>
        <div class="header-actions">
            <button id="automation-btn" class="btn" onclick="toggleAutomation()" style="background-color: var(--color-squirrel); color: white; box-shadow: 0 4px 12px rgba(16, 185, 129, 0.3); border: none;">
                <span id="automation-text">Automation: Active 🟢</span>
            </button>
            <div id="sync-indicator" style="display: flex; align-items: center; gap: 0.4rem; font-size: 0.8rem; color: var(--text-secondary); margin-right: 0.5rem;">
                <span style="width: 8px; height: 8px; background-color: var(--color-squirrel); border-radius: 50%; display: inline-block; box-shadow: 0 0 8px var(--color-squirrel); animation: pulse 2s infinite;"></span>
                <span>Auto-sync active</span>
            </div>
            <button id="undo-btn" class="btn" style="background-color: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: var(--text-primary); opacity: 0.5; cursor: not-allowed;" onclick="triggerUndo()" disabled>
                <span>Undo ↩️</span>
            </button>
            <button id="spray-btn" class="btn" style="background-color: var(--color-not-squirrel); color: white; box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3);" onclick="triggerSpray()">
                <span class="spinner" id="spray-spinner" style="border-left-color: white;"></span>
                <span class="btn-text" id="spray-text">Spray 💦</span>
            </button>
            <button id="sync-btn" class="btn btn-sync">
                <span class="spinner"></span>
                <span class="btn-text">Sync from Pi</span>
            </button>
        </div>
    </header>

    <div class="container">
        <div class="sidebar">
            <div class="card">
                <div class="card-title">Database Stats</div>
                <ul class="stats-list">
                    <li class="stat-item">
                        <span>Unclassified (raw):</span>
                        <span id="stat-raw" class="stat-val raw">-</span>
                    </li>
                    <li class="stat-item">
                        <span>Squirrels:</span>
                        <span id="stat-squirrel" class="stat-val squirrel">-</span>
                    </li>
                    <li class="stat-item">
                        <span>Not Squirrels:</span>
                        <span id="stat-not-squirrel" class="stat-val not-squirrel">-</span>
                    </li>
                </ul>
            </div>

            <div class="card">
                <div class="card-title">View Mode</div>
                <div style="display: flex; flex-direction: column; gap: 0.8rem;">
                    <div style="display: flex; flex-direction: column; gap: 0.5rem;">
                        <button id="mode-dashboard" class="btn" style="justify-content: center; background-color: var(--color-sync); color: white;" onclick="setViewMode('dashboard')">Dashboard 📊</button>
                        <button id="mode-queue" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('queue')">Classify Queue 📥</button>
                        <button id="mode-squirrel" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('squirrel')">Review Squirrels</button>
                        <button id="mode-not_squirrel" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('not_squirrel')">Review Not Squirrels</button>
                        <button id="mode-videos" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('videos')">Spray Videos 📹</button>
                        <button id="mode-train" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('train')">Train Model 🧠</button>
                        <button id="mode-settings" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('settings')">Settings ⚙️</button>
                        <button id="mode-logs" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('logs')">Classifier Logs 📋</button>
                    </div>
                    <label style="display: flex; align-items: center; gap: 0.6rem; font-size: 0.85rem; cursor: pointer; color: var(--text-secondary); margin-top: 0.2rem; border-top: 1px solid var(--border-color); padding-top: 0.6rem;">
                        <input type="checkbox" id="reverse-toggle" checked onchange="toggleReverse(this.checked)" style="width: 16px; height: 16px; accent-color: var(--color-sync); cursor: pointer;">
                        <span>Newest first (reverse order)</span>
                    </label>
                </div>
            </div>

            <div class="card">
                <div class="card-title">Auto-Labeler</div>
                <div style="display: flex; flex-direction: column; gap: 0.5rem;">
                    <label style="display: flex; align-items: center; gap: 0.6rem; font-size: 0.9rem; cursor: pointer; color: var(--text-primary);">
                        <input type="checkbox" id="gemini-toggle" style="width: 18px; height: 18px; accent-color: var(--color-sync); border-radius: 4px; cursor: pointer;">
                        <span>Use Gemini 2.5 Flash</span>
                    </label>
                </div>
            </div>

            <div class="card">
                <div class="card-title">Keyboard Shortcuts</div>
                <ul class="shortcuts-list">
                    <li class="shortcut-item">
                        <span>Squirrel</span>
                        <span class="key-badge">▶</span>
                    </li>
                    <li class="shortcut-item">
                        <span>Not Squirrel</span>
                        <span class="key-badge">◀</span>
                    </li>
                    <li class="shortcut-item">
                        <span>Trash / Delete</span>
                        <span class="key-badge">▼</span>
                    </li>
                    <li class="shortcut-item">
                        <span>Undo Last Action</span>
                        <span class="key-badge">Z</span>
                    </li>
                    <li class="shortcut-item">
                        <span>Manual Spray</span>
                        <span class="key-badge">Space</span>
                    </li>
                    <li class="shortcut-item">
                        <span>Previous Page / Image</span>
                        <span class="key-badge">[</span>
                    </li>
                    <li class="shortcut-item">
                        <span>Next Page / Image</span>
                        <span class="key-badge">]</span>
                    </li>
                    <li class="shortcut-item">
                        <span>Close Modal</span>
                        <span class="key-badge">Esc</span>
                    </li>
                </ul>
            </div>
        </div>

        <div class="main-workspace">
            <div class="card viewer-card" id="workspace-card">
                <!-- Content injected dynamically -->
            </div>
        </div>
    </div>

    <!-- --- Image Expand Modal --- -->
    <div id="image-modal" class="modal" onclick="closeImageModal()">
        <div class="modal-content" onclick="event.stopPropagation()">
            <span class="close-btn" onclick="closeImageModal()">&times;</span>
            <div class="image-container" id="modal-img-container" style="height: 500px; max-height: 60vh; border: none; box-shadow: none;">
                <img id="modal-img-element" src="" alt="Expanded preview">
            </div>
            <div id="modal-img-filename" class="image-filename" style="margin-top: 1rem; text-align: center;"></div>
            
            <div style="display: flex; align-items: center; justify-content: center; gap: 1rem; margin-top: 1rem;">
                <button class="btn" style="padding: 0.4rem 0.8rem; font-size: 0.8rem; background-color: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: var(--text-primary);" onclick="navigateModal(-1)" id="modal-prev-btn">&lt; Prev</button>
                <span style="font-size: 0.9rem; color: var(--text-secondary); font-weight: 600;" id="modal-image-counter">
                    Image - of -
                </span>
                <button class="btn" style="padding: 0.4rem 0.8rem; font-size: 0.8rem; background-color: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: var(--text-primary);" onclick="navigateModal(1)" id="modal-next-btn">Next &gt;</button>
            </div>

            <div class="action-buttons" style="margin-top: 1.5rem;">
                <button class="btn btn-not-squirrel" onclick="classifyModal('not_squirrel')">◀ Not Squirrel</button>
                <button class="btn btn-delete" onclick="classifyModal('delete')">▼ Trash</button>
                <button class="btn btn-squirrel" onclick="classifyModal('squirrel')">Squirrel ▶</button>
            </div>
        </div>
    </div>

    <!-- --- Video Player Modal --- -->
    <div id="video-modal" class="modal" onclick="closeVideoModal()">
        <div class="modal-content" onclick="event.stopPropagation()">
            <span class="close-btn" onclick="closeVideoModal()">&times;</span>
            <div style="width: 100%; height: 480px; max-height: 60vh; background: #020617; border-radius: 12px; overflow: hidden; display: flex; align-items: center; justify-content: center; border: 1px solid var(--border-color);">
                <video id="modal-video-element" controls autoplay loop style="max-width: 100%; max-height: 100%; object-fit: contain;"></video>
            </div>
            <div id="modal-video-filename" class="image-filename" style="margin-top: 1rem; text-align: center;"></div>
            <div id="modal-video-classification-actions" style="display: flex; justify-content: center; gap: 1rem; margin-top: 1rem; margin-bottom: 0.5rem;"></div>
        </div>
    </div>

    <script>
        let currentImage = null; // Used in queue mode
        let viewMode = 'dashboard';
        let reverseOrder = true;
        let currentIndex = 0; // For queue mode
        let totalCount = 0; // For queue mode

        // Gallery & Video variables
        let galleryImages = []; // List of images on the current page
        let currentPage = 1;
        let totalPages = 1;
        let galleryTotalCount = 0;
        let modalIndex = 0; // Index of the currently open image in the galleryImages array
        let videoClassifications = {};
        let videoFavorites = {};

        function toggleReverse(checked) {
            reverseOrder = checked;
            // Reset to page 1 on sorting change
            currentPage = 1;
            loadNext();
        }

        function setPage(page) {
            if (page < 1 || page > totalPages) return;
            currentPage = page;
            loadNext();
        }

        function navigatePage(delta) {
            if (viewMode === 'queue') {
                let targetIdx = currentIndex + delta;
                if (targetIdx < 0) targetIdx = 0;
                if (targetIdx >= totalCount) targetIdx = totalCount - 1;
                loadNext('', targetIdx);
            }
        }

        function setViewMode(mode) {
            viewMode = mode;
            currentPage = 1; // Reset page
            
            // If navigating away from train and training is not active, clear polling
            if (mode !== 'train' && !isTrainingRunning && trainPollingInterval) {
                clearInterval(trainPollingInterval);
                trainPollingInterval = null;
            }
            // If navigating away from logs, clear polling
            if (mode !== 'logs' && logsPollingInterval) {
                clearInterval(logsPollingInterval);
                logsPollingInterval = null;
            }
            
            const modes = ['dashboard', 'queue', 'squirrel', 'not_squirrel', 'videos', 'train', 'settings', 'logs'];
            modes.forEach(m => {
                const btn = document.getElementById(`mode-${m}`);
                if (btn) {
                    if (m === mode) {
                        btn.style.backgroundColor = 'var(--color-sync)';
                        btn.style.color = 'white';
                        btn.style.border = 'none';
                    } else {
                        btn.style.backgroundColor = 'transparent';
                        btn.style.color = 'var(--text-secondary)';
                        btn.style.border = '1px solid var(--border-color)';
                    }
                }
            });
            loadNext();
        }

        function updateUndoButtonState(hasHistory) {
            const btn = document.getElementById('undo-btn');
            if (btn) {
                btn.disabled = !hasHistory;
                if (hasHistory) {
                    btn.style.opacity = '1';
                    btn.style.cursor = 'pointer';
                } else {
                    btn.style.opacity = '0.5';
                    btn.style.cursor = 'not-allowed';
                }
            }
        }

        function updateAutomationButton(enabled) {
            const btn = document.getElementById('automation-btn');
            const txt = document.getElementById('automation-text');
            if (!btn || !txt) return;
            if (enabled) {
                txt.innerText = 'Automation: Active 🟢';
                btn.style.backgroundColor = 'var(--color-squirrel)';
                btn.style.color = 'white';
                btn.style.boxShadow = '0 4px 12px rgba(16, 185, 129, 0.3)';
                btn.style.border = 'none';
            } else {
                txt.innerText = 'Automation: Paused 🔴';
                btn.style.backgroundColor = 'rgba(255, 255, 255, 0.05)';
                btn.style.color = 'var(--text-secondary)';
                btn.style.boxShadow = 'none';
                btn.style.border = '1px solid var(--border-color)';
            }
        }

        async function fetchAutomationStatus() {
            try {
                const res = await fetch('/api/automation_status');
                const data = await res.json();
                updateAutomationButton(data.enabled);
            } catch (e) {
                console.error("Error fetching automation status:", e);
            }
        }

        async function toggleAutomation() {
            try {
                const res = await fetch('/api/toggle_automation', { method: 'POST' });
                const data = await res.json();
                updateAutomationButton(data.enabled);
            } catch (e) {
                console.error("Error toggling automation:", e);
            }
        }

        let blastsChart = null;

        async function renderDashboardView() {
            const workspace = document.getElementById('workspace-card');
            workspace.innerHTML = `
                <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1.5rem;">
                    <h2 style="font-weight: 600; font-size: 1.25rem;">System Dashboard 📊</h2>
                    <span style="font-size: 0.85rem; font-weight: 600; padding: 0.25rem 0.75rem; border-radius: 20px; background-color: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3);">Live Mode</span>
                </div>

                <div class="dash-grid">
                    <div class="dash-card blasted">
                        <div class="dash-card-label">Squirrels Blasted</div>
                        <div class="dash-card-val" id="dash-blasted-count">...</div>
                        <div class="dash-card-sub" id="dash-blasted-sub">Auto: ... | Manual: ...</div>
                    </div>
                    <div class="dash-card status">
                        <div class="dash-card-label">Solenoid Controller</div>
                        <div class="dash-card-val" id="dash-system-status">Checking...</div>
                        <div class="dash-card-sub" id="dash-system-status-sub">Automation Loop</div>
                    </div>
                    <div class="dash-card queue">
                        <div class="dash-card-label">Classify Queue</div>
                        <div class="dash-card-val" id="dash-queue-count">...</div>
                        <div class="dash-card-sub" id="dash-queue-sub">Raw images waiting</div>
                    </div>
                    <div class="dash-card accuracy">
                        <div class="dash-card-label">Inference Accuracy</div>
                        <div class="dash-card-val" id="dash-accuracy-rate">-%</div>
                        <div class="dash-card-sub" id="dash-accuracy-sub">Accurate: 0 | False Pos: 0</div>
                    </div>
                </div>

                <div class="dash-row">
                    <div class="dash-panel">
                        <div class="dash-panel-title">
                            <span>Blast Activity (Last 7 Days)</span>
                            <span style="font-size: 0.8rem; color: var(--text-secondary); font-weight: normal;">Water Spray Events</span>
                        </div>
                        <div style="flex-grow: 1; position: relative;">
                            <canvas id="blasts-chart"></canvas>
                        </div>
                    </div>
                    <div class="dash-panel">
                        <div class="dash-panel-title">
                            <span>Live Snapshot Feed</span>
                            <span style="font-size: 0.85rem; padding: 0.1rem 0.4rem; border-radius: 6px; background-color: rgba(59, 130, 246, 0.15); color: #60a5fa; cursor: pointer; border: 1px solid rgba(59, 130, 246, 0.25);" onclick="refreshDashboardSnapshot()">Refresh 🔄</span>
                        </div>
                        <div class="dash-feed-container">
                            <div class="dash-feed-overlay" id="dash-feed-overlay">
                                <span class="dot"></span>
                                <span>LIVE</span>
                            </div>
                            <img id="dash-feed-img" src="/api/latest_image?t=${Date.now()}" onerror="this.src='https://images.unsplash.com/photo-1542273917363-3b1817f69a2d?auto=format&fit=crop&w=800&q=80'; console.warn('No camera snap available');">
                        </div>
                        <div style="margin-top: 1rem; font-size: 0.8rem; color: var(--text-secondary); line-height: 1.4; text-align: center;">
                            Updates automatically every 15s. Continuous streaming is disabled to prevent Raspberry Pi camera lock contention.
                        </div>
                    </div>
                </div>
            `;
            await updateDashboardData();
        }

        async function refreshDashboardSnapshot() {
            const img = document.getElementById('dash-feed-img');
            if (img) {
                img.src = `/api/latest_image?t=${Date.now()}`;
            }
        }

        async function updateDashboardData() {
            if (viewMode !== 'dashboard') return;
            try {
                const statsRes = await fetch('/api/next_image?mode=queue');
                const statsData = await statsRes.json();
                
                if (statsData.stats) {
                    document.getElementById('stat-raw').innerText = statsData.stats.raw_count;
                    document.getElementById('stat-squirrel').innerText = statsData.stats.squirrel_count;
                    document.getElementById('stat-not-squirrel').innerText = statsData.stats.not_squirrel_count;
                    
                    const qVal = document.getElementById('dash-queue-count');
                    const qSub = document.getElementById('dash-queue-sub');
                    if (qVal) qVal.innerText = statsData.stats.raw_count;
                    if (qSub) qSub.innerText = `${statsData.stats.raw_count} raw images remaining`;

                    const hour = statsData.stats.current_hour;
                    const mtime = statsData.stats.latest_image_mtime * 1000;
                    const now = Date.now();
                    const ageSeconds = (now - mtime) / 1000;
                    
                    const overlay = document.getElementById('dash-feed-overlay');
                    if (overlay) {
                        if (hour < 6 || hour >= 20) {
                            overlay.innerHTML = `<span class="dot" style="background-color: var(--color-delete); box-shadow: 0 0 8px var(--color-delete); animation: none;"></span><span>SLEEPING (Night)</span>`;
                            overlay.title = "Camera is currently sleeping (6:00 AM - 8:00 PM Eastern active hours).";
                        } else if (mtime > 0 && ageSeconds > 300) {
                            overlay.innerHTML = `<span class="dot" style="background-color: var(--color-not-squirrel); box-shadow: 0 0 8px var(--color-not-squirrel); animation: none;"></span><span>IDLE / OFFLINE</span>`;
                            overlay.title = "No capture received in the last 5 minutes. Pi may be offline or camera is idle.";
                        } else {
                            overlay.innerHTML = `<span class="dot" style="background-color: var(--color-squirrel); box-shadow: 0 0 8px var(--color-squirrel);"></span><span>LIVE</span>`;
                            overlay.title = "Camera is active and sending images.";
                        }
                    }
                }

                const autoRes = await fetch('/api/automation_status');
                const autoData = await autoRes.json();
                
                const statusVal = document.getElementById('dash-system-status');
                const statusSub = document.getElementById('dash-system-status-sub');
                if (statusVal) {
                    statusVal.innerText = autoData.enabled ? 'ACTIVE 🟢' : 'PAUSED 🔴';
                    statusVal.style.color = autoData.enabled ? 'var(--color-squirrel)' : 'var(--color-not-squirrel)';
                }
                if (statusSub) {
                    statusSub.innerText = autoData.enabled ? 'Repeller ready to blast' : 'Manual overrides only';
                }

                const blastsRes = await fetch('/api/blasts');
                const blastsData = await blastsRes.json();

                const blastedVal = document.getElementById('dash-blasted-count');
                const blastedSub = document.getElementById('dash-blasted-sub');
                if (blastedVal) blastedVal.innerText = blastsData.total_blasts;
                if (blastedSub) blastedSub.innerText = `Auto: ${blastsData.auto_blasts} | Manual: ${blastsData.manual_blasts}`;

                const accuracyVal = document.getElementById('dash-accuracy-rate');
                const accuracySub = document.getElementById('dash-accuracy-sub');
                if (accuracyVal) {
                    accuracyVal.innerText = blastsData.accuracy_rate !== null ? `${blastsData.accuracy_rate}%` : '-%';
                }
                if (accuracySub) {
                    accuracySub.innerText = `Accurate: ${blastsData.classified_accurate || 0} | False Pos: ${blastsData.classified_false_positive || 0}`;
                }

                renderBlastsChart(blastsData.blasts);
                refreshDashboardSnapshot();
            } catch (e) {
                console.error("Error updating dashboard data:", e);
            }
        }

        function renderBlastsChart(blasts) {
            const ctx = document.getElementById('blasts-chart');
            if (!ctx) return;

            const days = [];
            const autoCounts = [];
            const manualCounts = [];

            for (let i = 6; i >= 0; i--) {
                const d = new Date();
                d.setDate(d.getDate() - i);
                const dateStr = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
                
                const label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                days.push(label);

                const dayBlasts = blasts.filter(b => b.timestamp && b.timestamp.startsWith(dateStr));
                autoCounts.push(dayBlasts.filter(b => b.type === 'auto').length);
                manualCounts.push(dayBlasts.filter(b => b.type === 'manual').length);
            }

            if (blastsChart) {
                blastsChart.destroy();
            }

            blastsChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: days,
                    datasets: [
                        {
                            label: 'Auto Detections',
                            data: autoCounts,
                            backgroundColor: 'rgba(16, 185, 129, 0.7)',
                            borderColor: 'var(--color-squirrel)',
                            borderWidth: 1,
                            borderRadius: 6
                        },
                        {
                            label: 'Manual Sprays',
                            data: manualCounts,
                            backgroundColor: 'rgba(59, 130, 246, 0.7)',
                            borderColor: 'var(--color-sync)',
                            borderWidth: 1,
                            borderRadius: 6
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            grid: { color: 'rgba(255, 255, 255, 0.05)' },
                            ticks: {
                                color: '#94a3b8',
                                font: { family: 'Outfit' }
                            }
                        },
                        y: {
                            grid: { color: 'rgba(255, 255, 255, 0.05)' },
                            ticks: {
                                color: '#94a3b8',
                                precision: 0,
                                font: { family: 'Outfit' }
                            },
                            beginAtZero: true
                        }
                    },
                    plugins: {
                        legend: {
                            labels: {
                                color: '#f8fafc',
                                font: { family: 'Outfit', weight: 'bold' }
                            }
                        },
                        tooltip: {
                            titleFont: { family: 'Outfit' },
                            bodyFont: { family: 'Outfit' }
                        }
                    }
                }
            });
        }

        function renderSettingsView() {
            const workspace = document.getElementById('workspace-card');
            workspace.innerHTML = `
                <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1.5rem;">
                    <h2 style="font-weight: 600; font-size: 1.25rem;">General Settings ⚙️</h2>
                    <span id="settings-save-badge" style="display: none; font-size: 0.85rem; font-weight: 600; padding: 0.25rem 0.75rem; border-radius: 20px; background-color: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3);">Saved successfully!</span>
                </div>
                
                <div style="width: 100%; max-width: 600px; display: flex; flex-direction: column; gap: 1.25rem;">
                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Pi Camera Capture Interval (seconds)</label>
                        <input type="number" id="settings-interval" min="5" max="300" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">How often the Pi Camera captures and sends stills for inference. Default: 15s.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Gemini API Key</label>
                        <input type="password" id="settings-gemini-key" placeholder="Enter Gemini API key for Auto-Labeling" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">Used by the Google GenAI library for automated raw image tagging.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Pi Camera Rotation (Degrees)</label>
                        <select id="settings-rotation" style="background: #0f172a; border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem; cursor: pointer;">
                            <option value="0">0° (Default)</option>
                            <option value="90">90°</option>
                            <option value="180">180°</option>
                            <option value="270">270°</option>
                        </select>
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">Flips the camera orientation captured by raspistill on the Pi.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Camera Region of Interest (ROI)</label>
                        <input type="text" id="settings-roi" placeholder="x,y,w,h (e.g. 0.05,0.15,0.3,0.3)" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">Digital zoom region from 0.0 to 1.0 (x,y,width,height) for still captures. Set empty to disable.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Video Region of Interest (Video ROI)</label>
                        <input type="text" id="settings-video-roi" placeholder="x,y,w,h (e.g. 0.0,0.0,0.6,0.6)" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">Digital zoom region from 0.0 to 1.0 (x,y,width,height) for video recordings. Set empty to disable.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">SQUIRREL Detection Confidence Threshold</label>
                        <input type="number" id="settings-confidence" min="0.50" max="0.99" step="0.05" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">Model probability threshold (0.50 - 0.99) required to trigger a water blast. Default: 0.70.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Spray Cooldown (seconds)</label>
                        <input type="number" id="settings-cooldown" min="0" max="600" step="5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">Minimum time to wait between sprays. Default: 60s.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Standard Spray Duration (seconds)</label>
                        <input type="number" id="settings-spray-duration" min="1" max="10" step="0.5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">The standard duration the solenoid is open. Default: 3.0s.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Inactivity Extended Spray Duration (seconds)</label>
                        <input type="number" id="settings-long-duration" min="1" max="20" step="0.5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">The spray duration used when the system has been idle, to clear air/pressure drops in the hose. Default: 5.0s.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Inactivity Threshold (hours)</label>
                        <input type="number" id="settings-threshold" min="0.5" max="24" step="0.5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">The amount of hours of inactivity required before triggering the extended spray duration. Default: 2.0 hours.</span>
                    </div>

                    <div style="border-top: 1px solid var(--border-color); padding-top: 1.25rem; margin-top: 0.5rem; display: flex; flex-direction: column; gap: 1.25rem;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary); margin-bottom: -0.25rem;">Notification Settings Alert 🔔</h3>
                        
                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Notification Channel</label>
                            <select id="settings-notif-type" style="background: #0f172a; border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem; cursor: pointer;">
                                <option value="none">None (Disabled)</option>
                                <option value="join">Joaoapps Join Push Alert</option>
                                <option value="email">Local SMTP Email Alert</option>
                                <option value="both">Both (Join Push & Email)</option>
                            </select>
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Select the alerts channel for water spray trigger notifications.</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Joaoapps Join API Key</label>
                            <input type="text" id="settings-join-key" placeholder="Enter Join API Key" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Your Join api key to receive pushes on your Android/browser devices.</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Local SMTP Server IP & Port</label>
                            <input type="text" id="settings-smtp-server" placeholder="192.169.86.113:25" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Address of your local network mail server. Default: 192.169.86.113:25.</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Email Recipient Address (To)</label>
                            <input type="email" id="settings-email-to" placeholder="e.g. nolan@example.com" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">The destination address where mail notifications will be sent.</span>
                        </div>
                    </div>

                    <div style="border-top: 1px solid var(--border-color); padding-top: 1.25rem; margin-top: 0.5rem; display: flex; flex-direction: column; gap: 1.25rem;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary); margin-bottom: -0.25rem;">Storage Retention Settings 🧹</h3>
                        
                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Raw Queue Image Retention (days)</label>
                            <input type="number" id="settings-retention-raw" min="0.1" max="90" step="0.1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">How long to keep unclassified raw captures in the review queue. Default: 3.0 days.</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Negative Sample (Not Squirrel) Retention (days)</label>
                            <input type="number" id="settings-retention-ns" min="0.1" max="90" step="0.1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Pruning threshold for negative birdfeeder frames to prevent bloating. Default: 7.0 days.</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Negative Sample Minimum Count</label>
                            <input type="number" id="settings-retention-ns-min" min="5" max="10000" step="5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Always preserve at least this many of the newest negative images for ML retraining, even if they are older than the retention threshold. Default: 1000.</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Trash Retention (days)</label>
                            <input type="number" id="settings-retention-trash" min="0.1" max="30" step="0.1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">How long to keep files in the local trash before permanently emptying them. Default: 1.0 day.</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Video Recording Retention (days)</label>
                            <input type="number" id="settings-retention-videos" min="0.1" max="180" step="0.1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">How long to keep recorded spray event videos. Default: 14.0 days.</span>
                        </div>
                    </div>

                    <div style="margin-top: 1rem;">
                        <button id="save-settings-btn" class="btn" style="background-color: var(--color-sync); color: white; box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);" onclick="saveSettings()">
                            <span class="spinner" id="settings-spinner" style="display: none; border-left-color: white;"></span>
                            <span class="btn-text" id="settings-btn-text">Save Settings 💾</span>
                        </button>
                    </div>
                </div>
            `;
            fetchSettings();
        }

        async function fetchSettings() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                if (data.status === 'success') {
                    document.getElementById('settings-interval').value = data.settings.capture_interval;
                    document.getElementById('settings-gemini-key').value = data.settings.gemini_api_key;
                    document.getElementById('settings-rotation').value = data.settings.camera_rotation;
                    document.getElementById('settings-roi').value = data.settings.camera_roi;
                    document.getElementById('settings-video-roi').value = data.settings.video_roi || '';
                    document.getElementById('settings-confidence').value = data.settings.confidence_threshold;
                    document.getElementById('settings-cooldown').value = data.settings.spray_cooldown_seconds || 60;
                    document.getElementById('settings-spray-duration').value = data.settings.spray_duration || 3.0;
                    document.getElementById('settings-long-duration').value = data.settings.long_spray_duration || 5.0;
                    document.getElementById('settings-threshold').value = data.settings.long_spray_threshold_hours || 2.0;
                    document.getElementById('settings-retention-raw').value = data.settings.retention_days_raw || 3.0;
                    document.getElementById('settings-retention-ns').value = data.settings.retention_days_not_squirrel || 7.0;
                    document.getElementById('settings-retention-ns-min').value = data.settings.retention_min_not_squirrel || 1000;
                    document.getElementById('settings-retention-trash').value = data.settings.retention_days_trash || 1.0;
                    document.getElementById('settings-retention-videos').value = data.settings.retention_days_videos || 14.0;
                    document.getElementById('settings-notif-type').value = data.settings.notification_type || 'none';
                    document.getElementById('settings-join-key').value = data.settings.join_api_key || '';
                    document.getElementById('settings-smtp-server').value = data.settings.email_smtp_server || '';
                    document.getElementById('settings-email-to').value = data.settings.email_to || '';
                }
            } catch (e) {
                console.error("Error fetching settings:", e);
            }
        }

        async function saveSettings() {
            const btn = document.getElementById('save-settings-btn');
            const btnText = document.getElementById('settings-btn-text');
            const spinner = document.getElementById('settings-spinner');
            const badge = document.getElementById('settings-save-badge');

            btn.disabled = true;
            spinner.style.display = 'inline-block';
            btnText.innerText = 'Saving...';
            badge.style.display = 'none';

            const capture_interval = parseInt(document.getElementById('settings-interval').value);
            const gemini_api_key = document.getElementById('settings-gemini-key').value;
            const camera_rotation = parseInt(document.getElementById('settings-rotation').value);
            const camera_roi = document.getElementById('settings-roi').value;
            const video_roi = document.getElementById('settings-video-roi').value;
            const confidence_threshold = parseFloat(document.getElementById('settings-confidence').value);
            const spray_cooldown_seconds = parseInt(document.getElementById('settings-cooldown').value);
            const spray_duration = parseFloat(document.getElementById('settings-spray-duration').value);
            const long_spray_duration = parseFloat(document.getElementById('settings-long-duration').value);
            const long_spray_threshold_hours = parseFloat(document.getElementById('settings-threshold').value);
            const retention_days_raw = parseFloat(document.getElementById('settings-retention-raw').value);
            const retention_days_not_squirrel = parseFloat(document.getElementById('settings-retention-ns').value);
            const retention_min_not_squirrel = parseInt(document.getElementById('settings-retention-ns-min').value);
            const retention_days_trash = parseFloat(document.getElementById('settings-retention-trash').value);
            const retention_days_videos = parseFloat(document.getElementById('settings-retention-videos').value);
            const notification_type = document.getElementById('settings-notif-type').value;
            const join_api_key = document.getElementById('settings-join-key').value;
            const email_smtp_server = document.getElementById('settings-smtp-server').value;
            const email_to = document.getElementById('settings-email-to').value;

            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        capture_interval,
                        gemini_api_key,
                        camera_rotation,
                        camera_roi,
                        video_roi,
                        confidence_threshold,
                        spray_cooldown_seconds,
                        spray_duration,
                        long_spray_duration,
                        long_spray_threshold_hours,
                        retention_days_raw,
                        retention_days_not_squirrel,
                        retention_min_not_squirrel,
                        retention_days_trash,
                        retention_days_videos,
                        notification_type,
                        join_api_key,
                        email_smtp_server,
                        email_to
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    badge.style.display = 'inline-block';
                    setTimeout(() => {
                        badge.style.display = 'none';
                    }, 4000);
                } else {
                    alert("Error saving settings: " + data.message);
                }
            } catch (e) {
                console.error("Error saving settings:", e);
                alert("Failed to save settings.");
            } finally {
                spinner.style.display = 'none';
                btnText.innerText = 'Save Settings 💾';
                btn.disabled = false;
            }
        }

        let trainPollingInterval = null;
        let logsPollingInterval = null;
        let isTrainingRunning = false;

        function renderTrainView() {
            const workspace = document.getElementById('workspace-card');
            workspace.innerHTML = `
                <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1.5rem;">
                    <h2 style="font-weight: 600; font-size: 1.25rem;">Train Model 🧠</h2>
                    <span id="train-status-badge" style="font-size: 0.85rem; font-weight: 600; padding: 0.25rem 0.75rem; border-radius: 20px; background-color: rgba(255,255,255,0.05); color: var(--text-secondary); border: 1px solid var(--border-color);">Status: Checking...</span>
                </div>
                
                <div style="background: rgba(2, 6, 23, 0.4); border: 1px solid var(--border-color); padding: 1.5rem; border-radius: 16px; margin-bottom: 1.5rem; backdrop-filter: blur(10px);">
                    <h3 style="font-size: 1.1rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-primary);">Finetune ResNet-18 Classifier</h3>
                    <p style="font-size: 0.9rem; color: var(--text-secondary); margin-bottom: 1.25rem; line-height: 1.5;">
                        This will retrain the AI model locally using all categorized images inside the squirrel and not_squirrel dataset folders. 
                        The training runs in the background and will automatically reload the new weights upon successful completion.
                    </p>
                    <button id="start-train-btn" class="btn" style="background-color: var(--color-sync); color: white; box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);" onclick="startTraining()">
                        <span class="spinner" id="train-spinner" style="display: none; border-left-color: white;"></span>
                        <span class="btn-text" id="train-btn-text">Start Training 🚀</span>
                    </button>
                </div>

                <div style="display: flex; flex-direction: column; gap: 0.5rem; flex-grow: 1; height: 350px; min-height: 250px;">
                    <div style="font-weight: 600; font-size: 0.9rem; color: var(--text-secondary);">Training Output Logs</div>
                    <pre id="train-logs" style="flex-grow: 1; overflow-y: auto; background-color: #020617; border: 1px solid var(--border-color); padding: 1rem; border-radius: 12px; font-family: monospace; font-size: 0.85rem; color: #a7f3d0; white-space: pre-wrap; line-height: 1.4;"></pre>
                </div>
            `;
            
            // Check status immediately
            checkTrainStatus();
            
            // Start polling if not already polling
            if (!trainPollingInterval) {
                trainPollingInterval = setInterval(checkTrainStatus, 1000);
            }
        }

        async function checkTrainStatus() {
            try {
                const res = await fetch('/api/train/status');
                const data = await res.json();
                
                isTrainingRunning = data.running;
                
                // Update badge
                const badge = document.getElementById('train-status-badge');
                const btn = document.getElementById('start-train-btn');
                const btnText = document.getElementById('train-btn-text');
                const spinner = document.getElementById('train-spinner');
                const logsPre = document.getElementById('train-logs');
                
                if (badge) {
                    if (data.running) {
                        badge.innerText = 'Status: Training... ⚙️';
                        badge.style.backgroundColor = 'rgba(59, 130, 246, 0.15)';
                        badge.style.color = '#60a5fa';
                        badge.style.borderColor = 'rgba(59, 130, 246, 0.3)';
                    } else if (data.exit_code === 0) {
                        badge.innerText = 'Status: Finished Successfully! ✅';
                        badge.style.backgroundColor = 'rgba(16, 185, 129, 0.15)';
                        badge.style.color = '#34d399';
                        badge.style.borderColor = 'rgba(16, 185, 129, 0.3)';
                    } else if (data.exit_code !== null) {
                        badge.innerText = `Status: Failed (Exit Code: ${data.exit_code}) ❌`;
                        badge.style.backgroundColor = 'rgba(239, 68, 68, 0.15)';
                        badge.style.color = '#f87171';
                        badge.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                    } else {
                        badge.innerText = 'Status: Idle 💤';
                        badge.style.backgroundColor = 'rgba(255,255,255,0.05)';
                        badge.style.color = 'var(--text-secondary)';
                        badge.style.borderColor = 'var(--border-color)';
                    }
                }
                
                if (btn && btnText && spinner) {
                    if (data.running) {
                        btn.disabled = true;
                        btn.style.opacity = '0.7';
                        btn.style.cursor = 'not-allowed';
                        btnText.innerText = 'Training...';
                        spinner.style.display = 'inline-block';
                    } else {
                        btn.disabled = false;
                        btn.style.opacity = '1';
                        btn.style.cursor = 'pointer';
                        btnText.innerText = 'Start Training 🚀';
                        spinner.style.display = 'none';
                    }
                }
                
                // Update logs
                if (logsPre && viewMode === 'train') {
                    // Detect if user was scrolled to bottom
                    const wasScrolledToBottom = logsPre.scrollHeight - logsPre.clientHeight <= logsPre.scrollTop + 20;
                    
                    logsPre.innerText = data.logs || 'No log output yet.';
                    
                    if (wasScrolledToBottom) {
                        logsPre.scrollTop = logsPre.scrollHeight;
                    }
                }
                
                // Clear interval if training stops and we are not in train tab
                if (!data.running && viewMode !== 'train' && trainPollingInterval) {
                    clearInterval(trainPollingInterval);
                    trainPollingInterval = null;
                }
            } catch (e) {
                console.error("Error checking training status:", e);
            }
        }

        async function startTraining() {
            try {
                const res = await fetch('/api/train/start', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    checkTrainStatus();
                } else {
                    alert(data.message);
                }
            } catch (e) {
                console.error("Error starting training:", e);
            }
        }

        async function triggerUndo() {
            try {
                const res = await fetch('/api/undo', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    document.getElementById('stat-raw').innerText = data.stats.raw_count;
                    document.getElementById('stat-squirrel').innerText = data.stats.squirrel_count;
                    document.getElementById('stat-not-squirrel').innerText = data.stats.not_squirrel_count;
                    
                    updateUndoButtonState(data.has_history);
                    
                    const isModalOpen = document.getElementById('image-modal').classList.contains('show');
                    
                    if (viewMode === 'queue') {
                        // Pass true to specify we want to display the undone image as the active image
                        await loadNext(data.undone_image, null, true);
                    } else {
                        await loadNext();
                        if (isModalOpen && data.undone_image) {
                            const idx = galleryImages.indexOf(data.undone_image);
                            if (idx !== -1) {
                                modalIndex = idx;
                                updateModalContent();
                            }
                        }
                    }
                } else {
                    console.warn("Undo failed:", data.message);
                }
            } catch (e) {
                console.error("Error triggering undo:", e);
            }
        }

        function renderLogsView() {
            const workspace = document.getElementById('workspace-card');
            workspace.innerHTML = `
                <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1.5rem;">
                    <h2 style="font-weight: 600; font-size: 1.25rem;">Classifier Logs 📋</h2>
                    <div style="display: flex; gap: 0.75rem;">
                        <button id="clear-logs-btn" class="btn" style="background-color: var(--color-delete); color: white;" onclick="clearClassifierLogs()">
                            Clear Logs 🗑️
                        </button>
                        <button id="refresh-logs-btn" class="btn" style="background-color: var(--color-sync); color: white;" onclick="fetchClassifierLogs()">
                            Refresh 🔄
                        </button>
                    </div>
                </div>
                
                <div style="display: flex; flex-direction: column; gap: 0.5rem; flex-grow: 1; height: 500px; min-height: 350px;">
                    <pre id="classifier-logs" style="flex-grow: 1; overflow-y: auto; background-color: #020617; border: 1px solid var(--border-color); padding: 1rem; border-radius: 12px; font-family: monospace; font-size: 0.85rem; color: #a7f3d0; white-space: pre-wrap; line-height: 1.4;"></pre>
                </div>
            `;
            
            // Fetch logs immediately
            fetchClassifierLogs();
            
            // Start polling if not already polling
            if (!logsPollingInterval) {
                logsPollingInterval = setInterval(fetchClassifierLogs, 3000);
            }
        }

        async function fetchClassifierLogs() {
            const logsPre = document.getElementById('classifier-logs');
            if (!logsPre) return;
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                
                // Detect if user was scrolled to bottom
                const wasScrolledToBottom = logsPre.scrollHeight - logsPre.clientHeight <= logsPre.scrollTop + 20;
                
                logsPre.innerText = data.logs || 'No classifier log output yet.';
                
                if (wasScrolledToBottom) {
                    logsPre.scrollTop = logsPre.scrollHeight;
                }
            } catch (e) {
                console.error("Error fetching classifier logs:", e);
            }
        }

        async function clearClassifierLogs() {
            if (!confirm("Are you sure you want to clear the classifier logs?")) return;
            try {
                const res = await fetch('/api/logs/clear', { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    fetchClassifierLogs();
                } else {
                    alert("Failed to clear logs: " + data.error);
                }
            } catch (e) {
                console.error("Error clearing logs:", e);
                alert("Error clearing logs: " + e);
            }
        }

        async function loadNext(current = '', index = null, showCurrent = false) {
            if (viewMode === 'logs') {
                renderLogsView();
                return;
            }
            if (viewMode === 'dashboard') {
                renderDashboardView();
                return;
            }
            if (viewMode === 'settings') {
                renderSettingsView();
                return;
            }
            if (viewMode === 'train') {
                renderTrainView();
                return;
            }
            if (viewMode === 'queue') {
                let url = `/api/next_image?mode=${viewMode}&reverse=${reverseOrder}`;
                if (index !== null) {
                    url += `&index=${index}`;
                } else if (current) {
                    url += `&current=${current}`;
                    if (showCurrent) {
                        url += `&show_current=true`;
                    }
                }
                
                const res = await fetch(url);
                const data = await res.json();
                
                // Update stats
                document.getElementById('stat-raw').innerText = data.stats.raw_count;
                document.getElementById('stat-squirrel').innerText = data.stats.squirrel_count;
                document.getElementById('stat-not-squirrel').innerText = data.stats.not_squirrel_count;
                updateUndoButtonState(data.has_history);
                
                const workspace = document.getElementById('workspace-card');
                
                if (data.image) {
                    currentImage = data.image;
                    currentIndex = data.index;
                    totalCount = data.total;
                    
                    workspace.innerHTML = `
                        <div class="image-container" id="img-container">
                            <img src="/image/${data.image}" alt="Feeder image">
                        </div>
                        <div class="image-filename" style="margin-top: 1rem; font-family: monospace; font-size: 0.9rem; color: var(--text-secondary); text-align: center;">
                            ${data.image}
                        </div>
                        
                        <div style="display: flex; align-items: center; justify-content: center; gap: 1rem; margin-top: 0.75rem;">
                            <button class="btn" style="padding: 0.4rem 0.8rem; font-size: 0.8rem; background-color: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: var(--text-primary);" onclick="navigatePage(-1)" ${currentIndex === 0 ? 'disabled' : ''}>&lt; Prev</button>
                            <span style="font-size: 0.9rem; color: var(--text-secondary); font-weight: 600;">
                                Image ${currentIndex + 1} of ${totalCount}
                            </span>
                            <button class="btn" style="padding: 0.4rem 0.8rem; font-size: 0.8rem; background-color: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: var(--text-primary);" onclick="navigatePage(1)" ${currentIndex === totalCount - 1 ? 'disabled' : ''}>Next &gt;</button>
                        </div>

                        <div class="action-buttons" style="margin-top: 1.5rem;">
                            <button class="btn btn-not-squirrel" onclick="classify('not_squirrel')">◀ Not Squirrel</button>
                            <button class="btn btn-delete" onclick="classify('delete')">▼ Trash</button>
                            <button class="btn btn-squirrel" onclick="classify('squirrel')">Squirrel ▶</button>
                        </div>
                    `;
                } else {
                    currentImage = null;
                    currentIndex = 0;
                    totalCount = 0;
                    workspace.innerHTML = `
                        <div class="no-images">
                            <div class="no-images-icon">🎉</div>
                            <h2>All images classified!</h2>
                            <p>Click "Sync from Pi" to download more stills.</p>
                        </div>
                    `;
                }
            } else if (viewMode === 'squirrel' || viewMode === 'not_squirrel') {
                // Fetch paginated list
                let url = `/api/list_images?mode=${viewMode}&page=${currentPage}&reverse=${reverseOrder}&per_page=12`;
                const res = await fetch(url);
                const data = await res.json();
                
                // Update stats
                document.getElementById('stat-raw').innerText = data.stats.raw_count;
                document.getElementById('stat-squirrel').innerText = data.stats.squirrel_count;
                document.getElementById('stat-not-squirrel').innerText = data.stats.not_squirrel_count;
                updateUndoButtonState(data.has_history);
                
                currentPage = data.page;
                totalPages = data.total_pages;
                galleryImages = data.images;
                galleryTotalCount = data.total_images;
                
                const workspace = document.getElementById('workspace-card');
                
                if (galleryImages.length > 0) {
                    let cardsHtml = '';
                    galleryImages.forEach((img, idx) => {
                        let overlayButtons = '';
                        if (viewMode === 'squirrel') {
                            overlayButtons = `
                                <button class="action-icon-btn" onclick="event.stopPropagation(); quickClassify('${img}', 'not_squirrel')" title="Move to Not Squirrel">❌</button>
                                <button class="action-icon-btn btn-delete-quick" onclick="event.stopPropagation(); quickClassify('${img}', 'delete')" title="Move to Trash">🗑️</button>
                            `;
                        } else {
                            overlayButtons = `
                                <button class="action-icon-btn" onclick="event.stopPropagation(); quickClassify('${img}', 'squirrel')" title="Move to Squirrel">🐿️</button>
                                <button class="action-icon-btn btn-delete-quick" onclick="event.stopPropagation(); quickClassify('${img}', 'delete')" title="Move to Trash">🗑️</button>
                            `;
                        }
                        
                        cardsHtml += `
                            <div class="gallery-card" onclick="openImageModal(${idx})">
                                <div class="card-actions-overlay">${overlayButtons}</div>
                                <img src="/image/${img}" alt="Still preview">
                                <div class="gallery-card-info">${img}</div>
                            </div>
                        `;
                    });
                    
                    // Build page links
                    let pageLinksHtml = `
                        <button class="page-link" onclick="setPage(1)" ${currentPage === 1 ? 'disabled' : ''}>&lt;&lt;</button>
                        <button class="page-link" onclick="setPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''}>&lt;</button>
                    `;
                    
                    let startPage = Math.max(1, currentPage - 2);
                    let endPage = Math.min(totalPages, startPage + 4);
                    if (endPage - startPage < 4) {
                        startPage = Math.max(1, endPage - 4);
                    }
                    
                    for (let p = startPage; p <= endPage; p++) {
                        pageLinksHtml += `
                            <button class="page-link ${p === currentPage ? 'active' : ''}" onclick="setPage(${p})">${p}</button>
                        `;
                    }
                    
                    pageLinksHtml += `
                        <button class="page-link" onclick="setPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''}>&gt;</button>
                        <button class="page-link" onclick="setPage(${totalPages})" ${currentPage === totalPages ? 'disabled' : ''}>&gt;&gt;</button>
                    `;
                    
                    let titleText = viewMode === 'squirrel' ? "Reviewed Squirrels" : "Reviewed Not Squirrels";
                    
                    workspace.innerHTML = `
                        <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1rem;">
                            <h2 style="font-weight: 600; font-size: 1.25rem;">${titleText}</h2>
                            <span style="font-size: 0.9rem; color: var(--text-secondary);">
                                Showing ${(currentPage - 1) * 12 + 1} - ${Math.min(currentPage * 12, galleryTotalCount)} of ${galleryTotalCount}
                            </span>
                        </div>
                        <div class="grid-gallery">
                            ${cardsHtml}
                        </div>
                        <div class="pagination-container">
                            ${pageLinksHtml}
                        </div>
                    `;
                } else {
                    let messageTitle = viewMode === 'squirrel' ? "No squirrels to review!" : "No images to review here!";
                    let messageDesc = viewMode === 'squirrel' ? "Classified squirrel images will appear here." : "Classified 'Not Squirrel' images will appear here.";
                    workspace.innerHTML = `
                        <div class="no-images">
                            <div class="no-images-icon">📸</div>
                            <h2>${messageTitle}</h2>
                            <p>${messageDesc}</p>
                        </div>
                    `;
                }
            } else if (viewMode === 'videos') {
                // Fetch videos
                let url = `/api/list_videos?reverse=${reverseOrder}`;
                const res = await fetch(url);
                const data = await res.json();
                
                // Update stats
                document.getElementById('stat-raw').innerText = data.stats.raw_count;
                document.getElementById('stat-squirrel').innerText = data.stats.squirrel_count;
                document.getElementById('stat-not-squirrel').innerText = data.stats.not_squirrel_count;
                updateUndoButtonState(data.has_history);
                
                const workspace = document.getElementById('workspace-card');
                const videos = data.videos;
                videoClassifications = data.classifications || {};
                videoFavorites = data.favorites || {};
                
                if (videos && videos.length > 0) {
                    let cardsHtml = '';
                    videos.forEach((vid) => {
                        const currentClassification = videoClassifications[vid] || null;
                        const isAccurate = currentClassification === 'accurate';
                        const isFalsePositive = currentClassification === 'false_positive';
                        const isFav = videoFavorites[vid] || false;
                        
                        cardsHtml += `
                            <div class="gallery-card" onclick="openVideoModal('${vid}')">
                                <button class="action-icon-btn" 
                                        style="position: absolute; top: 8px; left: 8px; background: ${isFav ? 'rgba(245, 158, 11, 0.25)' : 'rgba(15, 23, 42, 0.6)'}; border: 1px solid ${isFav ? '#f59e0b' : 'var(--border-color)'}; color: ${isFav ? '#f59e0b' : 'var(--text-secondary)'}; border-radius: 8px; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 6; font-size: 1rem; transition: all 0.15s ease;"
                                        onclick="event.stopPropagation(); toggleFavoriteVideo('${vid}', ${!isFav})" 
                                        title="${isFav ? 'Unfavorite Video' : 'Favorite Video'}">
                                    ⭐
                                </button>
                                <div class="card-actions-overlay">
                                    <button class="action-icon-btn" onclick="event.stopPropagation(); shareVideo('${vid}')" title="Share Video Link" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); color: var(--text-secondary); border-radius: 8px; width: 32px; height: 32px; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; font-size: 1rem; margin-right: 0.2rem;">🔗</button>
                                    <a class="action-icon-btn" href="/video/${vid}" download="${vid}" onclick="event.stopPropagation()" title="Download Video" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); color: var(--text-secondary); border-radius: 8px; width: 32px; height: 32px; display: inline-flex; align-items: center; justify-content: center; cursor: pointer; text-decoration: none; font-size: 1rem; margin-right: 0.2rem;">📥</a>
                                    <button class="action-icon-btn btn-delete-quick" onclick="event.stopPropagation(); deleteVideo('${vid}')" title="Delete Video">🗑️</button>
                                </div>
                                <img src="/video/${vid.replace('.mp4', '.jpg')}" 
                                     alt="Video thumbnail" 
                                     style="width: 100%; height: 150px; object-fit: cover; border-bottom: 1px solid var(--border-color);"
                                     onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                                <div style="display: none; height: 150px; background: #020617; align-items: center; justify-content: center; font-size: 3rem; border-bottom: 1px solid var(--border-color);">
                                    📹
                                </div>
                                <div class="gallery-card-info" style="border-bottom: none;">${vid}</div>
                                <div style="display: flex; gap: 0.5rem; padding: 0.5rem 0.75rem 0.75rem 0.75rem; background: rgba(0, 0, 0, 0.2); border-top: 1px solid rgba(255,255,255,0.05);" onclick="event.stopPropagation()">
                                    <button class="btn btn-classify-video accurate-btn ${isAccurate ? 'active' : ''}" 
                                            style="flex: 1; padding: 0.35rem; font-size: 0.75rem; font-weight: 600; border-radius: 6px; border: 1px solid ${isAccurate ? 'var(--color-squirrel)' : 'rgba(255,255,255,0.1)'}; background-color: ${isAccurate ? 'rgba(16, 185, 129, 0.2)' : 'transparent'}; color: ${isAccurate ? 'var(--color-squirrel)' : 'var(--text-secondary)'}; cursor: pointer; transition: all 0.15s ease;"
                                            onclick="classifyVideo('${vid}', '${isAccurate ? '' : 'accurate'}')">
                                        Accurate 🐿️
                                    </button>
                                    <button class="btn btn-classify-video false-positive-btn ${isFalsePositive ? 'active' : ''}" 
                                            style="flex: 1; padding: 0.35rem; font-size: 0.75rem; font-weight: 600; border-radius: 6px; border: 1px solid ${isFalsePositive ? 'var(--color-not-squirrel)' : 'rgba(255,255,255,0.1)'}; background-color: ${isFalsePositive ? 'rgba(239, 68, 68, 0.2)' : 'transparent'}; color: ${isFalsePositive ? 'var(--color-not-squirrel)' : 'var(--text-secondary)'}; cursor: pointer; transition: all 0.15s ease;"
                                            onclick="classifyVideo('${vid}', '${isFalsePositive ? '' : 'false_positive'}')">
                                        False Pos ❌
                                    </button>
                                </div>
                            </div>
                        `;
                    });
                    
                    workspace.innerHTML = `
                        <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1rem;">
                            <h2 style="font-weight: 600; font-size: 1.25rem;">Spray Videos</h2>
                            <span style="font-size: 0.9rem; color: var(--text-secondary);">${videos.length} videos recorded</span>
                        </div>
                        <div class="grid-gallery">
                            ${cardsHtml}
                        </div>
                    `;
                } else {
                    workspace.innerHTML = `
                        <div class="no-images">
                            <div class="no-images-icon">📹</div>
                            <h2>No videos recorded yet</h2>
                            <p>Manual spray or automatic spray triggers will record videos.</p>
                        </div>
                    `;
                }
            }
        }

        async function classify(category) {
            if (!currentImage) return;
            const container = document.getElementById('img-container');
            if (container) {
                container.className = `image-container flash-${category.replace('_', '-')}`;
            }

            try {
                const res = await fetch('/api/classify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: currentImage, category })
                });
                const data = await res.json();
                updateUndoButtonState(data.has_history);
                setTimeout(() => loadNext(currentImage), 150);
            } catch (e) {
                console.error("Error classifying image:", e);
                loadNext();
            }
        }

        async function quickClassify(filename, category) {
            try {
                const res = await fetch('/api/classify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename, category })
                });
                const data = await res.json();
                updateUndoButtonState(data.has_history);
                loadNext();
            } catch (e) {
                console.error("Error quick classifying:", e);
            }
        }

        async function deleteVideo(filename) {
            if (!confirm(`Are you sure you want to delete video: ${filename}?`)) return;
            try {
                const res = await fetch('/api/delete_video', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    loadNext();
                } else {
                    alert("Delete failed: " + data.message);
                }
            } catch (e) {
                console.error("Error deleting video:", e);
            }
        }

        // --- Image Modal Functions ---
        function openImageModal(index) {
            modalIndex = index;
            updateModalContent();
            document.getElementById('image-modal').classList.add('show');
        }

        function closeImageModal() {
            document.getElementById('image-modal').classList.remove('show');
            loadNext(); // Refresh gallery view
        }

        async function loadPageAndOpenModal(pageChange, targetIndexType) {
            await loadNext();
            if (galleryImages.length > 0) {
                if (targetIndexType === 'first') {
                    modalIndex = 0;
                } else if (targetIndexType === 'last') {
                    modalIndex = galleryImages.length - 1;
                }
                updateModalContent();
            } else {
                closeImageModal();
            }
        }

        function updateModalContent() {
            if (modalIndex < 0 || modalIndex >= galleryImages.length) return;
            const img = galleryImages[modalIndex];
            document.getElementById('modal-img-element').src = `/image/${img}`;
            document.getElementById('modal-img-filename').innerText = img;
            document.getElementById('modal-image-counter').innerText = `Image ${(currentPage - 1) * 12 + modalIndex + 1} of ${galleryTotalCount}`;
            
            document.getElementById('modal-prev-btn').disabled = (currentPage === 1 && modalIndex === 0);
            document.getElementById('modal-next-btn').disabled = (currentPage === totalPages && modalIndex === galleryImages.length - 1);
        }

        async function navigateModal(direction) {
            let target = modalIndex + direction;
            if (target >= 0 && target < galleryImages.length) {
                modalIndex = target;
                updateModalContent();
            } else if (target < 0) {
                if (currentPage > 1) {
                    currentPage--;
                    await loadPageAndOpenModal(-1, 'last');
                }
            } else if (target >= galleryImages.length) {
                if (currentPage < totalPages) {
                    currentPage++;
                    await loadPageAndOpenModal(1, 'first');
                }
            }
        }

        async function classifyModal(category) {
            if (modalIndex < 0 || modalIndex >= galleryImages.length) return;
            const img = galleryImages[modalIndex];
            
            const container = document.getElementById('modal-img-container');
            if (container) {
                container.className = `image-container flash-${category.replace('_', '-')}`;
            }

            try {
                const res = await fetch('/api/classify', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: img, category })
                });
                const data = await res.json();
                updateUndoButtonState(data.has_history);
                
                // Remove item from frontend list
                galleryImages.splice(modalIndex, 1);
                
                setTimeout(() => {
                    if (container) {
                        container.className = `image-container`;
                    }
                    if (galleryImages.length === 0) {
                        closeImageModal();
                    } else {
                        if (modalIndex >= galleryImages.length) {
                            modalIndex = galleryImages.length - 1;
                        }
                        updateModalContent();
                    }
                }, 150);
            } catch (e) {
                console.error("Error classifying modal image:", e);
            }
        }

        // --- Video Modal Functions ---
        function openVideoModal(filename) {
            document.getElementById('modal-video-element').src = `/video/${filename}`;
            document.getElementById('modal-video-filename').innerText = filename;
            updateModalVideoClassifications(filename);
            document.getElementById('video-modal').classList.add('show');
        }

        function updateModalVideoClassifications(filename) {
            const container = document.getElementById('modal-video-classification-actions');
            if (!container) return;
            
            const currentClassification = videoClassifications[filename] || null;
            const isAccurate = currentClassification === 'accurate';
            const isFalsePositive = currentClassification === 'false_positive';
            const isFav = videoFavorites[filename] || false;
            
            container.innerHTML = `
                <button class="btn" 
                        style="padding: 0.5rem 1.25rem; font-weight: 600; border-radius: 8px; border: 1px solid ${isFav ? '#f59e0b' : 'rgba(255,255,255,0.1)'}; background-color: ${isFav ? 'rgba(245, 158, 11, 0.2)' : 'transparent'}; color: ${isFav ? '#f59e0b' : 'var(--text-secondary)'}; cursor: pointer; transition: all 0.15s ease;"
                        onclick="toggleFavoriteVideoModal('${filename}', ${!isFav})">
                    ⭐ ${isFav ? 'Favorited' : 'Favorite'}
                </button>
                <button class="btn" 
                        style="padding: 0.5rem 1.25rem; font-weight: 600; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background-color: transparent; color: var(--text-secondary); cursor: pointer; transition: all 0.15s ease;"
                        onclick="shareVideo('${filename}')">
                    🔗 Share
                </button>
                <a class="btn" href="/video/${filename}" download="${filename}"
                        style="text-decoration: none; display: inline-flex; align-items: center; justify-content: center; padding: 0.5rem 1.25rem; font-weight: 600; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); background-color: transparent; color: var(--text-secondary); cursor: pointer; transition: all 0.15s ease;">
                    📥 Download
                </a>
                <div style="width: 1px; height: 24px; background: rgba(255,255,255,0.1); margin: 0 0.5rem; align-self: center;"></div>
                <button class="btn" 
                        style="padding: 0.5rem 1.25rem; font-weight: 600; border-radius: 8px; border: 1px solid ${isAccurate ? 'var(--color-squirrel)' : 'rgba(255,255,255,0.1)'}; background-color: ${isAccurate ? 'rgba(16, 185, 129, 0.2)' : 'transparent'}; color: ${isAccurate ? 'var(--color-squirrel)' : 'var(--text-secondary)'}; cursor: pointer; transition: all 0.15s ease;"
                        onclick="classifyVideoModal('${filename}', '${isAccurate ? '' : 'accurate'}')">
                    Accurate 🐿️
                </button>
                <button class="btn" 
                        style="padding: 0.5rem 1.25rem; font-weight: 600; border-radius: 8px; border: 1px solid ${isFalsePositive ? 'var(--color-not-squirrel)' : 'rgba(255,255,255,0.1)'}; background-color: ${isFalsePositive ? 'rgba(239, 68, 68, 0.2)' : 'transparent'}; color: ${isFalsePositive ? 'var(--color-not-squirrel)' : 'var(--text-secondary)'}; cursor: pointer; transition: all 0.15s ease;"
                        onclick="classifyVideoModal('${filename}', '${isFalsePositive ? '' : 'false_positive'}')">
                    False Positive ❌
                </button>
            `;
        }

        async function shareVideo(filename) {
            const videoUrl = window.location.origin + `/video/${filename}`;
            if (navigator.share) {
                try {
                    await navigator.share({
                        title: 'Squirrel Soaker Spray Video',
                        text: `Check out this squirrel spray video: ${filename}`,
                        url: videoUrl
                    });
                } catch (e) {
                    console.error('Error sharing:', e);
                }
            } else {
                try {
                    await navigator.clipboard.writeText(videoUrl);
                    alert('Video link copied to clipboard! 🔗');
                } catch (err) {
                    alert('Could not copy link: ' + err);
                }
            }
        }

        async function toggleFavoriteVideo(filename, favorite) {
            try {
                const res = await fetch('/api/favorite_video', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_name: filename, favorite: favorite })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    videoFavorites[filename] = favorite;
                    if (viewMode === 'videos') {
                        loadNext();
                    }
                } else {
                    alert("Error: " + data.message);
                }
            } catch (e) {
                console.error("Error favoriting video:", e);
            }
        }

        async function toggleFavoriteVideoModal(filename, favorite) {
            await toggleFavoriteVideo(filename, favorite);
            updateModalVideoClassifications(filename);
        }

        async function classifyVideo(filename, classification) {
            try {
                const res = await fetch('/api/classify_video', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_name: filename, classification: classification || null })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    videoClassifications[filename] = classification || null;
                    if (viewMode === 'videos') {
                        // Triggers redrawing of the cards grid to reflect changes
                        loadNext();
                    }
                } else {
                    alert("Error: " + data.message);
                }
            } catch (e) {
                console.error("Error classifying video:", e);
            }
        }

        async function classifyVideoModal(filename, classification) {
            await classifyVideo(filename, classification);
            updateModalVideoClassifications(filename);
        }

        function closeVideoModal() {
            const player = document.getElementById('modal-video-element');
            player.pause();
            player.src = "";
            document.getElementById('video-modal').classList.remove('show');
        }

        async function triggerSpray() {
            const btn = document.getElementById('spray-btn');
            const text = document.getElementById('spray-text');
            const spinner = document.getElementById('spray-spinner');
            
            btn.disabled = true;
            spinner.style.display = 'inline-block';
            text.innerText = 'Spraying...';
            
            try {
                const res = await fetch('/api/spray', { method: 'POST' });
                const data = await res.json();
                if (data.status !== 'success') {
                    alert("Spray failed: " + data.message);
                }
            } catch (e) {
                console.error("Error triggering spray:", e);
                alert("Error sending spray command.");
            } finally {
                spinner.style.display = 'none';
                text.innerText = 'Spray 💦';
                btn.disabled = false;
            }
        }

        // Keyboard handler
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' || e.code === 'Escape') {
                if (document.getElementById('image-modal').classList.contains('show')) {
                    closeImageModal();
                }
                if (document.getElementById('video-modal').classList.contains('show')) {
                    closeVideoModal();
                }
                return;
            }
            if (e.key === 'z' || e.key === 'u' || e.key === 'Z' || e.key === 'U') {
                // Trigger undo
                triggerUndo();
                return;
            }
            if (e.key === ' ' || e.code === 'Space') {
                e.preventDefault(); // Prevent page scroll
                triggerSpray();
                return;
            }
            
            // Modal image shortcuts
            if (document.getElementById('image-modal').classList.contains('show')) {
                if (e.key === 'ArrowLeft') {
                    classifyModal('not_squirrel');
                } else if (e.key === 'ArrowRight') {
                    classifyModal('squirrel');
                } else if (e.key === 'ArrowDown' || e.key === 'Delete' || e.key === 'Backspace') {
                    classifyModal('delete');
                } else if (e.key === '[' || e.key === '{') {
                    navigateModal(-1);
                } else if (e.key === ']' || e.key === '}') {
                    navigateModal(1);
                }
                return;
            }

            // Normal queue mode shortcuts
            if (viewMode === 'queue') {
                if (e.key === '[' || e.key === '{') {
                    navigatePage(-1);
                    return;
                }
                if (e.key === ']' || e.key === '}') {
                    navigatePage(1);
                    return;
                }
                if (!currentImage) return;
                if (e.key === 'ArrowLeft') {
                    classify('not_squirrel');
                } else if (e.key === 'ArrowRight') {
                    classify('squirrel');
                } else if (e.key === 'ArrowDown' || e.key === 'Delete' || e.key === 'Backspace') {
                    classify('delete');
                }
            }
        });

        // Sync handler
        document.getElementById('sync-btn').addEventListener('click', async () => {
            const btn = document.getElementById('sync-btn');
            btn.classList.add('syncing');
            btn.disabled = true;
            
            try {
                const useGemini = document.getElementById('gemini-toggle').checked;
                const res = await fetch('/api/sync', { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ auto_label: useGemini })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    console.log("Sync succeeded.");
                } else {
                    alert("Sync failed: " + data.message);
                }
            } catch (e) {
                console.error("Error syncing:", e);
                alert("Error during sync operation.");
            } finally {
                btn.classList.remove('syncing');
                btn.disabled = false;
                loadNext();
            }
        });

        // Auto-sync function
        async function autoSync() {
            try {
                const useGemini = document.getElementById('gemini-toggle').checked;
                const res = await fetch('/api/sync', { 
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ auto_label: useGemini })
                });
                const data = await res.json();
                
                if (data.stats) {
                    document.getElementById('stat-raw').innerText = data.stats.raw_count;
                    document.getElementById('stat-squirrel').innerText = data.stats.squirrel_count;
                    document.getElementById('stat-not-squirrel').innerText = data.stats.not_squirrel_count;
                    
                    if (viewMode === 'dashboard') {
                        updateDashboardData();
                    } else if (viewMode === 'queue' && !currentImage && data.stats.raw_count > 0) {
                        loadNext();
                    }
                }
            } catch (e) {
                console.error("Auto-sync background error:", e);
            }
        }

        setInterval(autoSync, 15000);
        loadNext();
        fetchAutomationStatus();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/favicon.ico')
def serve_favicon():
    svg_icon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🐿️</text></svg>"""
    return svg_icon, 200, {'Content-Type': 'image/svg+xml'}

@app.route('/image/<filename>')
def serve_image(filename):
    for d in [RAW_DIR, SQUIRREL_DIR, NOT_SQUIRREL_DIR, TRASH_DIR]:
        if os.path.exists(os.path.join(d, filename)):
            return send_from_directory(d, filename)
    return "Image not found", 404

@app.route('/video/<filename>')
def serve_video(filename):
    return send_from_directory(VIDEOS_DIR, filename)

@app.route('/api/next_image')
def next_image():
    mode = request.args.get('mode', 'queue')
    current = request.args.get('current', '')
    reverse = request.args.get('reverse', 'false') == 'true'
    index_str = request.args.get('index', '')
    show_current = request.args.get('show_current', 'false') == 'true'
    
    if mode == 'squirrel':
        files = sorted([f for f in os.listdir(SQUIRREL_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))], reverse=reverse)
    elif mode == 'not_squirrel':
        files = sorted([f for f in os.listdir(NOT_SQUIRREL_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))], reverse=reverse)
    else:
        files = sorted([f for f in os.listdir(RAW_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))], reverse=reverse)
        
    image = None
    current_idx = 0
    total = len(files)
    
    if total > 0:
        if index_str.isdigit():
            idx = int(index_str)
            if 0 <= idx < total:
                image = files[idx]
                current_idx = idx
          # If show_current is true and filename matches, show it directly. Otherwise show next.
        elif current and current in files:
            idx = files.index(current)
            if show_current:
                image = files[idx]
                current_idx = idx
            elif idx + 1 < total:
                image = files[idx + 1]
                current_idx = idx + 1
            else:
                image = files[idx]
                current_idx = idx
        else:
            image = files[0]
            current_idx = 0
            
    return jsonify({
        'image': image,
        'index': current_idx,
        'total': total,
        'stats': get_stats(),
        'has_history': len(classification_history) > 0
    })

@app.route('/api/list_images')
def list_images():
    mode = request.args.get('mode', 'queue')
    reverse = request.args.get('reverse', 'false') == 'true'
    try:
        page = int(request.args.get('page', 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get('per_page', 12))
    except ValueError:
        per_page = 12
        
    if mode == 'squirrel':
        files = sorted([f for f in os.listdir(SQUIRREL_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))], reverse=reverse)
    elif mode == 'not_squirrel':
        files = sorted([f for f in os.listdir(NOT_SQUIRREL_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))], reverse=reverse)
    else:
        files = sorted([f for f in os.listdir(RAW_DIR) if f.lower().endswith(('.jpg', '.jpeg', '.png'))], reverse=reverse)
        
    total_images = len(files)
    total_pages = (total_images + per_page - 1) // per_page if total_images > 0 else 1
    
    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages
        
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_files = files[start_idx:end_idx]
    
    return jsonify({
        'images': page_files,
        'page': page,
        'per_page': per_page,
        'total_images': total_images,
        'total_pages': total_pages,
        'stats': get_stats(),
        'has_history': len(classification_history) > 0
    })

@app.route('/api/list_videos')
def list_videos():
    import json
    import datetime
    reverse = request.args.get('reverse', 'false') == 'true'
    if os.path.exists(VIDEOS_DIR):
        files = sorted([f for f in os.listdir(VIDEOS_DIR) if f.lower().endswith('.mp4')], reverse=reverse)
    else:
        files = []
        
    classifications = {}
    favorites = {}
    blasts = []
    if os.path.exists(BLASTS_LOG_FILE):
        try:
            with open(BLASTS_LOG_FILE, 'r') as f:
                blasts = json.load(f)
                if not isinstance(blasts, list):
                    blasts = []
        except Exception as e:
            print("Error reading blasts log:", e)
            
    for f in files:
        video_time = get_video_timestamp(f)
        if not video_time:
            continue
        for entry in blasts:
            entry_time_str = entry.get('timestamp')
            if not entry_time_str:
                continue
            try:
                entry_time = datetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
                if abs((video_time - entry_time).total_seconds()) < 6.0:
                    if 'classification' in entry:
                        classifications[f] = entry['classification']
                    if 'favorite' in entry:
                        favorites[f] = entry['favorite']
                    break
            except Exception:
                continue
                
    return jsonify({
        'videos': files,
        'classifications': classifications,
        'favorites': favorites,
        'stats': get_stats(),
        'has_history': len(classification_history) > 0
    })

@app.route('/api/delete_video', methods=['POST'])
def delete_video():
    data = request.get_json() or {}
    filename = data.get('filename')
    if not filename:
        return jsonify({'status': 'error', 'message': 'Missing filename'}), 400
        
    filepath = os.path.join(VIDEOS_DIR, filename)
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
            return jsonify({'status': 'success', 'stats': get_stats(), 'has_history': len(classification_history) > 0})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 500
    return jsonify({'status': 'error', 'message': 'Video not found'}), 404

@app.route('/api/classify_video', methods=['POST'])
def classify_video():
    import json
    import datetime
    data = request.get_json() or {}
    video_name = data.get('video_name')
    classification = data.get('classification') # 'accurate', 'false_positive', or null
    
    if not video_name:
        return jsonify({'status': 'error', 'message': 'Missing video_name'}), 400
        
    if classification not in [None, 'accurate', 'false_positive']:
        return jsonify({'status': 'error', 'message': 'Invalid classification'}), 400
        
    video_time = get_video_timestamp(video_name)
    if not video_time:
        return jsonify({'status': 'error', 'message': 'Invalid video filename format'}), 400
        
    # Read blasts log
    blasts = []
    if os.path.exists(BLASTS_LOG_FILE):
        try:
            with open(BLASTS_LOG_FILE, 'r') as f:
                blasts = json.load(f)
                if not isinstance(blasts, list):
                    blasts = []
        except Exception as e:
            print("Error reading blasts log:", e)
            
    # Find closest entry
    closest_entry = None
    min_diff = 6.0 # 5 second threshold
    
    for entry in blasts:
        entry_time_str = entry.get('timestamp')
        if not entry_time_str:
            continue
        try:
            entry_time = datetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
            diff = abs((video_time - entry_time).total_seconds())
            if diff < min_diff:
                min_diff = diff
                closest_entry = entry
        except Exception:
            continue
            
    if closest_entry:
        if classification:
            closest_entry['classification'] = classification
        else:
            closest_entry.pop('classification', None)
    else:
        # Fallback: create a new entry if classification is provided
        if classification:
            new_entry = {
                'timestamp': video_time.strftime("%Y-%m-%d %H:%M:%S"),
                'type': 'auto',
                'classification': classification
            }
            blasts.append(new_entry)
            
    # Save blasts log
    try:
        with open(BLASTS_LOG_FILE, 'w') as f:
            json.dump(blasts, f, indent=2)
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Error writing to blasts log: {0}'.format(e)}), 500
        
    return jsonify({
        'status': 'success',
        'stats': get_stats(),
        'has_history': len(classification_history) > 0
    })

@app.route('/api/favorite_video', methods=['POST'])
def favorite_video():
    import json
    import datetime
    data = request.get_json() or {}
    video_name = data.get('video_name')
    favorite = data.get('favorite', False) # True or False
    
    if not video_name:
        return jsonify({'status': 'error', 'message': 'Missing video_name'}), 400
        
    video_time = get_video_timestamp(video_name)
    if not video_time:
        return jsonify({'status': 'error', 'message': 'Invalid video filename format'}), 400
        
    # Read blasts log
    blasts = []
    if os.path.exists(BLASTS_LOG_FILE):
        try:
            with open(BLASTS_LOG_FILE, 'r') as f:
                blasts = json.load(f)
                if not isinstance(blasts, list):
                    blasts = []
        except Exception as e:
            print("Error reading blasts log:", e)
            
    # Find closest entry
    closest_entry = None
    min_diff = 6.0 # 5 second threshold
    
    for entry in blasts:
        entry_time_str = entry.get('timestamp')
        if not entry_time_str:
            continue
        try:
            entry_time = datetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
            diff = abs((video_time - entry_time).total_seconds())
            if diff < min_diff:
                min_diff = diff
                closest_entry = entry
        except Exception:
            continue
            
    if closest_entry:
        if favorite:
            closest_entry['favorite'] = True
        else:
            closest_entry.pop('favorite', None)
    else:
        # Fallback: create a new entry if favorited
        if favorite:
            new_entry = {
                'timestamp': video_time.strftime("%Y-%m-%d %H:%M:%S"),
                'type': 'auto',
                'favorite': True
            }
            blasts.append(new_entry)
            
    # Save blasts log
    try:
        with open(BLASTS_LOG_FILE, 'w') as f:
            json.dump(blasts, f, indent=2)
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Error writing to blasts log: {0}'.format(e)}), 500
        
    return jsonify({
        'status': 'success',
        'stats': get_stats(),
        'has_history': len(classification_history) > 0
    })


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'POST':
        data = request.get_json() or {}
        settings = load_settings()
        try:
            if 'capture_interval' in data:
                settings['capture_interval'] = int(data['capture_interval'])
            if 'gemini_api_key' in data:
                settings['gemini_api_key'] = str(data['gemini_api_key']).strip()
            if 'camera_rotation' in data:
                settings['camera_rotation'] = int(data['camera_rotation'])
            if 'camera_roi' in data:
                settings['camera_roi'] = str(data['camera_roi']).strip()
            if 'video_roi' in data:
                settings['video_roi'] = str(data['video_roi']).strip()
            if 'confidence_threshold' in data:
                settings['confidence_threshold'] = float(data['confidence_threshold'])
            if 'spray_cooldown_seconds' in data:
                settings['spray_cooldown_seconds'] = int(data['spray_cooldown_seconds'])
            if 'notification_type' in data:
                settings['notification_type'] = str(data['notification_type']).strip()
            if 'join_api_key' in data:
                settings['join_api_key'] = str(data['join_api_key']).strip()
            if 'email_smtp_server' in data:
                settings['email_smtp_server'] = str(data['email_smtp_server']).strip()
            if 'email_to' in data:
                settings['email_to'] = str(data['email_to']).strip()
            if 'spray_duration' in data:
                settings['spray_duration'] = float(data['spray_duration'])
            if 'long_spray_duration' in data:
                settings['long_spray_duration'] = float(data['long_spray_duration'])
            if 'long_spray_threshold_hours' in data:
                settings['long_spray_threshold_hours'] = float(data['long_spray_threshold_hours'])
            if 'retention_days_raw' in data:
                settings['retention_days_raw'] = float(data['retention_days_raw'])
            if 'retention_days_not_squirrel' in data:
                settings['retention_days_not_squirrel'] = float(data['retention_days_not_squirrel'])
            if 'retention_min_not_squirrel' in data:
                settings['retention_min_not_squirrel'] = int(data['retention_min_not_squirrel'])
            if 'retention_days_trash' in data:
                settings['retention_days_trash'] = float(data['retention_days_trash'])
            if 'retention_days_videos' in data:
                settings['retention_days_videos'] = float(data['retention_days_videos'])
                
            save_settings(settings)
            
            import threading
            def run_once():
                try:
                    raw_days = settings.get('retention_days_raw', 3.0)
                    ns_days = settings.get('retention_days_not_squirrel', 7.0)
                    ns_min = settings.get('retention_min_not_squirrel', 1000)
                    trash_days = settings.get('retention_days_trash', 1.0)
                    vid_days = settings.get('retention_days_videos', 14.0)
                    
                    log_message("[Storage Cleanup] Running settings-change cleanup...")
                    del_raw = clean_directory_by_age(RAW_DIR, raw_days)
                    del_trash = clean_directory_by_age(TRASH_DIR, trash_days)
                    del_vid = clean_videos_directory(VIDEOS_DIR, vid_days)
                    del_ns = clean_not_squirrel_directory(NOT_SQUIRREL_DIR, ns_days, ns_min)
                    
                    if del_raw > 0 or del_trash > 0 or del_vid > 0 or del_ns > 0:
                        log_message("[Storage Cleanup] Done. Deleted: raw={0}, trash={1}, videos={2}, not_squirrel={3}".format(
                            del_raw, del_trash, del_vid, del_ns
                        ))
                    else:
                        log_message("[Storage Cleanup] Done. No files needed pruning.")
                except Exception as e:
                    log_message("Error in settings-change cleanup thread: {0}".format(e))
                    
            threading.Thread(target=run_once).start()
            
            return jsonify({'status': 'success', 'settings': settings})
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400
    else:
        return jsonify({'status': 'success', 'settings': load_settings()})

@app.route('/api/blasts')
def get_blasts():
    import json
    blasts = []
    if os.path.exists(BLASTS_LOG_FILE):
        try:
            with open(BLASTS_LOG_FILE, 'r') as f:
                blasts = json.load(f)
                if not isinstance(blasts, list):
                    blasts = []
        except Exception as e:
            print("Error reading blasts log:", e)
            
    accurate_count = len([b for b in blasts if b.get('classification') == 'accurate'])
    false_positive_count = len([b for b in blasts if b.get('classification') == 'false_positive'])
    total_classified = accurate_count + false_positive_count
    accuracy_rate = round((accurate_count / total_classified) * 100, 1) if total_classified > 0 else None
            
    return jsonify({
        'blasts': blasts,
        'total_blasts': len(blasts),
        'auto_blasts': len([b for b in blasts if b.get('type') == 'auto']),
        'manual_blasts': len([b for b in blasts if b.get('type') == 'manual']),
        'classified_accurate': accurate_count,
        'classified_false_positive': false_positive_count,
        'accuracy_rate': accuracy_rate
    })

@app.route('/api/latest_image')
def latest_image():
    from flask import send_from_directory
    latest_file = None
    latest_mtime = 0
    latest_dir = None
    
    for d in [RAW_DIR, SQUIRREL_DIR, NOT_SQUIRREL_DIR]:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                path = os.path.join(d, f)
                try:
                    mtime = os.path.getmtime(path)
                    if mtime > latest_mtime:
                        latest_mtime = mtime
                        latest_file = f
                        latest_dir = d
                except Exception:
                    pass
                    
    if latest_file and latest_dir:
        response = send_from_directory(latest_dir, latest_file)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    else:
        return "No image found", 404

@app.route('/api/classify', methods=['POST'])

def classify():
    data = request.get_json()
    filename = data.get('filename')
    category = data.get('category')
    
    if not filename or not category:
        return jsonify({'status': 'error', 'message': 'Missing filename or category'}), 400
        
    src_path = None
    src_dir = None
    for d in [RAW_DIR, SQUIRREL_DIR, NOT_SQUIRREL_DIR, TRASH_DIR]:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            src_path = p
            src_dir = d
            break
            
    if not src_path:
        return jsonify({'status': 'error', 'message': 'Image does not exist'}), 404
        
    # Determine target directory
    if category == 'squirrel':
        target_dir = SQUIRREL_DIR
    elif category == 'not_squirrel':
        target_dir = NOT_SQUIRREL_DIR
    elif category == 'delete':
        target_dir = TRASH_DIR
    else:
        return jsonify({'status': 'error', 'message': 'Invalid category'}), 400
        
    # Prevent moving to same folder
    if src_dir == target_dir:
        return jsonify({'status': 'success', 'stats': get_stats(), 'has_history': len(classification_history) > 0})
        
    try:
        shutil.move(src_path, os.path.join(target_dir, filename))
        
        # Save to history for undo
        classification_history.append({
            'filename': filename,
            'source_dir': src_dir,
            'target_dir': target_dir
        })
        if len(classification_history) > 50:
            classification_history.pop(0)
            
        return jsonify({
            'status': 'success',
            'stats': get_stats(),
            'has_history': True
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/undo', methods=['POST'])
def undo():
    if not classification_history:
        return jsonify({'status': 'error', 'message': 'No actions to undo'}), 400
        
    last_action = classification_history.pop()
    filename = last_action['filename']
    source_dir = last_action['source_dir']
    target_dir = last_action['target_dir']
    
    target_path = os.path.join(target_dir, filename)
    source_path = os.path.join(source_dir, filename)
    
    if os.path.exists(target_path):
        try:
            shutil.move(target_path, source_path)
            return jsonify({
                'status': 'success',
                'undone_image': filename,
                'stats': get_stats(),
                'has_history': len(classification_history) > 0
            })
        except Exception as e:
            classification_history.append(last_action)
            return jsonify({'status': 'error', 'message': str(e)}), 500
    else:
        return jsonify({'status': 'error', 'message': 'File no longer exists at target location'}), 404

@app.route('/api/sync', methods=['POST'])
def sync():
    data = request.get_json() or {}
    use_gemini = data.get('auto_label', False)
    
    try:
        script_path = os.path.join(BASE_DIR, 'sync_images.sh')
        res = subprocess.run([script_path], capture_output=True, text=True, check=True)
        
        # Process synced videos (convert raw .h264 to mp4)
        process_synced_videos()
        
        if use_gemini:
            python_executable = os.path.join(BASE_DIR, '.venv', 'bin', 'python3')
            labeler_script = os.path.join(BASE_DIR, 'auto_label.py')
            settings = load_settings()
            env = os.environ.copy()
            if settings.get('gemini_api_key'):
                env['GEMINI_API_KEY'] = settings['gemini_api_key']
            subprocess.run([python_executable, labeler_script], env=env, check=True)
        
        return jsonify({
            'status': 'success',
            'output': res.stdout,
            'stats': get_stats()
        })
    except subprocess.CalledProcessError as e:
        return jsonify({
            'status': 'error',
            'message': e.stderr or str(e)
        }), 500
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/api/spray', methods=['POST'])
def spray():
    global last_spray_time
    import urllib.request
    import urllib.parse
    import json
    is_test = request.args.get('test') == 'true'
    try:
        duration = get_current_spray_duration()
        settings = load_settings()
        rotation = settings.get('camera_rotation', 0)
        roi = settings.get('video_roi', '')
        encoded_roi = urllib.parse.quote(roi) if roi else ''
        url = 'http://{0}:8080/spray?duration={1}&rotation={2}&roi={3}'.format(PI_IP, duration, rotation, encoded_roi)
        req = urllib.request.Request(url, method='POST')
        with urllib.request.urlopen(req, timeout=25) as response:
            res_data = response.read().decode('utf-8')
            if not is_test:
                log_blast('manual')
                last_spray_time = time.time()
            return jsonify(json.loads(res_data))
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/automation_status')
def get_automation_status():
    global automation_enabled
    return jsonify({'enabled': automation_enabled})

@app.route('/api/toggle_automation', methods=['POST'])
def toggle_automation():
    global automation_enabled
    automation_enabled = not automation_enabled
    save_automation_status(automation_enabled)
    log_message("[Automation] Automation toggled to: {0}".format(automation_enabled))
    return jsonify({'enabled': automation_enabled})

@app.route('/api/train/start', methods=['POST'])
def start_training():
    global training_process, last_exit_code, model_reloaded
    # Check if already running
    if training_process is not None and training_process.poll() is None:
        return jsonify({'status': 'error', 'message': 'Training is already in progress.'})
        
    # Clear the log file
    log_path = os.path.join(BASE_DIR, 'data', 'train.log')
    try:
        with open(log_path, 'w') as f:
            f.write("Initializing local retraining...\n")
    except Exception as e:
        log_message("Error clearing train.log: {0}".format(e))
        
    # Start train.py as a subprocess using the same python interpreter
    import sys
    train_script = os.path.join(BASE_DIR, 'train.py')
    try:
        last_exit_code = None
        model_reloaded = False
        training_process = subprocess.Popen(
            [sys.executable, '-u', train_script],
            stdout=open(log_path, 'w'),
            stderr=subprocess.STDOUT
        )
        log_message("[Training] Started background training subprocess (PID: {0})".format(training_process.pid))
        return jsonify({'status': 'success', 'message': 'Training started.'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/train/status')
def train_status():
    global training_process, last_exit_code, model_reloaded
    running = False
    
    if training_process is not None:
        exit_code = training_process.poll()
        if exit_code is None:
            running = True
            last_exit_code = None
            model_reloaded = False
        else:
            last_exit_code = exit_code
            training_process = None  # Reset pointer since it completed
            
            # Hot-reload if successful
            if last_exit_code == 0 and not model_reloaded:
                try:
                    load_trained_model()
                    log_message("[Training] Hot-reloaded newly trained model successfully.")
                    model_reloaded = True
                except Exception as e:
                    log_message("Error hot-reloading weights: {0}".format(e))
                    
    # Read the log file
    log_path = os.path.join(BASE_DIR, 'data', 'train.log')
    logs = ""
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                logs = f.read()
        except Exception as e:
            logs = "Error reading log: {0}".format(str(e))
            
    return jsonify({
        'running': running,
        'exit_code': last_exit_code,
        'logs': logs
    })

@app.route('/api/logs')
def get_classifier_logs():
    # Return last 200 lines of classifier log file
    log_path = os.path.join(BASE_DIR, 'data', 'classifier.log')
    logs = ""
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                lines = f.readlines()
                logs = "".join(lines[-200:])
        except Exception as e:
            logs = "Error reading log: {0}".format(str(e))
    else:
        logs = "No log output yet."
    return jsonify({'logs': logs})

@app.route('/api/logs/clear', methods=['POST'])
def clear_classifier_logs():
    log_path = os.path.join(BASE_DIR, 'data', 'classifier.log')
    try:
        with log_lock:
            with open(log_path, 'w') as f:
                f.write("")
        log_message("Classifier log cleared manually.")
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/predict', methods=['POST'])
def predict():
    global automation_enabled, last_spray_time
    img_data = request.data
    if not img_data:
        return jsonify({'status': 'error', 'message': 'No image data received'}), 400
        
    is_test = request.args.get('test') == 'true'
    
    import datetime
    filename = "img_auto_{0}.jpg".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    filepath = os.path.join(RAW_DIR, filename)
    
    with open(filepath, 'wb') as f:
        f.write(img_data)
        
    # Perform prediction using model
    is_squirrel, confidence = model_predict(filepath)
    
    # Auto-classify if confidence is > 85%
    if confidence > 0.85:
        target_dir = SQUIRREL_DIR if is_squirrel else NOT_SQUIRREL_DIR
        try:
            shutil.move(filepath, os.path.join(target_dir, filename))
            log_message("[Auto-Classify] Automatically classified {0} as {1} (confidence: {2:.2f})".format(
                filename, 'squirrel' if is_squirrel else 'not_squirrel', confidence
            ))
        except Exception as e:
            log_message("Error auto-classifying {0}: {1}".format(filename, str(e)))
            
    # Check spray cooldown
    current_time = time.time()
    settings = load_settings()
    cooldown = float(settings.get('spray_cooldown_seconds', 60))
    cooldown_active = (current_time - last_spray_time < cooldown)
    
    # Override is_squirrel returned to Pi if automation is disabled or during cooldown
    if automation_enabled and is_squirrel and not cooldown_active:
        response_is_squirrel = True
    else:
        response_is_squirrel = False
        
    duration = get_current_spray_duration()
    
    if response_is_squirrel and confidence > 0.70:
        if not is_test:
            log_blast('auto', confidence)
            last_spray_time = current_time
            log_message("[Cooldown] Solenoid triggered. Cooldown activated for {0}s.".format(cooldown))
    elif is_squirrel and cooldown_active and automation_enabled:
        log_message("[Cooldown] Squirrel detected, but skipping spray because cooldown is active ({0:.1f}s remaining).".format(
            cooldown - (current_time - last_spray_time)
        ))
        
    return jsonify({
        'is_squirrel': response_is_squirrel,
        'confidence': confidence,
        'filename': filename,
        'automation_enabled': automation_enabled,
        'spray_duration': duration
    })

if __name__ == '__main__':
    import threading
    cleanup_thread = threading.Thread(target=run_storage_cleanup)
    cleanup_thread.daemon = True
    cleanup_thread.start()
    
    log_message("Starting Squirrel Soaker 9001 Classifier App...")
    log_message("Serving locally at http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=True)
