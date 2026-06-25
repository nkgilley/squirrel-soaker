import os
import time
import shutil
import subprocess
import mimetypes
from flask import Flask, jsonify, request, send_from_directory, render_template_string
import threading
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, select, update, delete, text
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session
import datetime
from collections import deque
from PIL import ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

app = Flask(__name__)

# --- Database Config & Models ---
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'squirrel_soaker.db')
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'), exist_ok=True)

engine = create_engine('sqlite:///' + DB_PATH, connect_args={'check_same_thread': False})
db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

Base = declarative_base()
Base.query = db_session.query_property()

class DBImage(Base):
    __tablename__ = 'images'
    id = Column(Integer, primary_key=True)
    filename = Column(String, unique=True, nullable=False, index=True)
    category = Column(String, nullable=False, index=True) # raw, squirrel, not_squirrel, trash
    captured_at = Column(DateTime, nullable=False, index=True)
    prediction_confidence = Column(Float, nullable=True)
    is_auto_classified = Column(Boolean, default=False)

class DBBlast(Base):
    __tablename__ = 'blasts'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    type = Column(String, nullable=False) # manual, auto
    confidence = Column(Float, nullable=True)
    duration = Column(Float, nullable=False)
    video_filename = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    classification = Column(String, nullable=True, index=True) # accurate, false_positive, null

class DBVideo(Base):
    __tablename__ = 'videos'
    id = Column(Integer, primary_key=True)
    filename = Column(String, unique=True, nullable=False, index=True)
    blast_id = Column(Integer, nullable=True, index=True)
    is_favorite = Column(Boolean, default=False, index=True)
    classification = Column(String, nullable=True, index=True) # legacy mirror of blasts.classification
    created_at = Column(DateTime, nullable=False, index=True)

class DBSetting(Base):
    __tablename__ = 'settings'
    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)

class DBUndoEvent(Base):
    __tablename__ = 'undo_events'
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    filename = Column(String, nullable=False)
    original_category = Column(String, nullable=False)
    target_category = Column(String, nullable=False)

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

# Create SQLite tables immediately on import to allow module-level database interactions
Base.metadata.create_all(bind=engine)

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

def upload_to_catbox(filepath):
    import urllib.request
    import uuid
    
    if not filepath or not os.path.exists(filepath):
        return None
        
    boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
    filename = os.path.basename(filepath)
    content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    
    try:
        with open(filepath, 'rb') as f:
            file_content = f.read()
            
        # Construct multipart form body with reqtype="fileupload" and fileToUpload
        body = (
            '--{0}\r\n'
            'Content-Disposition: form-data; name="reqtype"\r\n\r\n'
            'fileupload\r\n'
            '--{0}\r\n'
            'Content-Disposition: form-data; name="fileToUpload"; filename="{1}"\r\n'
            'Content-Type: {2}\r\n\r\n'
        ).format(boundary, filename, content_type).encode('utf-8')
        
        body += file_content
        body += '\r\n--{0}--\r\n'.format(boundary).encode('utf-8')
        
        headers = {
            'Content-Type': 'multipart/form-data; boundary={0}'.format(boundary),
            'Content-Length': str(len(body))
        }
        
        req = urllib.request.Request('https://catbox.moe/user/api.php', data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=20) as res:
            url = res.read().decode('utf-8').strip()
            log_message("[Upload] Synced file uploaded to catbox.moe: {0}".format(url))
            return url
    except Exception as e:
        log_message("[Upload] Error uploading file to catbox.moe: {0}".format(e))
        return None

def upload_to_transfersh(filepath):
    import urllib.request
    
    if not filepath or not os.path.exists(filepath):
        return None
        
    filename = os.path.basename(filepath)
    try:
        with open(filepath, 'rb') as f:
            file_content = f.read()
            
        req = urllib.request.Request(
            'https://transfer.sh/{0}'.format(filename), 
            data=file_content, 
            method='PUT'
        )
        with urllib.request.urlopen(req, timeout=20) as res:
            url = res.read().decode('utf-8').strip()
            log_message("[Upload] Synced file uploaded to transfer.sh: {0}".format(url))
            return url
    except Exception as e:
        log_message("[Upload] Error uploading file to transfer.sh: {0}".format(e))
        return None

def upload_to_0x0(filepath):
    import urllib.request
    import uuid
    
    if not filepath or not os.path.exists(filepath):
        return None
        
    boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
    filename = os.path.basename(filepath)
    content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    
    try:
        with open(filepath, 'rb') as f:
            file_content = f.read()
            
        # Construct multipart form body
        body = (
            '--{0}\r\n'
            'Content-Disposition: form-data; name="file"; filename="{1}"\r\n'
            'Content-Type: {2}\r\n\r\n'
        ).format(boundary, filename, content_type).encode('utf-8')
        
        body += file_content
        body += '\r\n--{0}--\r\n'.format(boundary).encode('utf-8')
        
        headers = {
            'Content-Type': 'multipart/form-data; boundary={0}'.format(boundary),
            'Content-Length': str(len(body))
        }
        
        req = urllib.request.Request('https://0x0.st', data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=20) as res:
            url = res.read().decode('utf-8').strip()
            log_message("[Upload] Synced file uploaded to 0x0.st: {0}".format(url))
            return url
    except Exception as e:
        log_message("[Upload] Error uploading file to 0x0.st: {0}".format(e))
        return None

def upload_file_to_public_host(filepath):
    log_message("[Upload] Starting public upload for {0}...".format(os.path.basename(filepath)))
    
    # 1. Try Catbox
    url = upload_to_catbox(filepath)
    if url:
        return url
        
    # 2. Try Transfer.sh
    url = upload_to_transfersh(filepath)
    if url:
        return url
        
    # 3. Try 0x0.st (fallback)
    url = upload_to_0x0(filepath)
    if url:
        return url
        
    log_message("[Upload] All upload providers failed.")
    return None

def upload_video_to_public_host(filepath):
    return upload_file_to_public_host(filepath)

def resolve_image_path(filename):
    if not filename:
        return None
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        return None
    for img_dir in [SQUIRREL_DIR, RAW_DIR, NOT_SQUIRREL_DIR, TRASH_DIR]:
        img_path = os.path.join(img_dir, safe_filename)
        if os.path.exists(img_path):
            return img_path
    return None

def build_image_url(filename, base_url):
    image_path = resolve_image_path(filename)
    if not image_path:
        return None, None
    public_url = upload_file_to_public_host(image_path)
    if public_url:
        return public_url, image_path
    return "{0}/image/{1}".format(base_url, filename), image_path

# --- ML Model Configuration ---
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model.pth')
MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'models')
os.makedirs(MODELS_DIR, exist_ok=True)

# Copy default model checkpoints if needed
try:
    pth_files = [f for f in os.listdir(MODELS_DIR) if f.endswith('.pth')]
    if not pth_files and os.path.exists(MODEL_PATH):
        shutil.copy2(MODEL_PATH, os.path.join(MODELS_DIR, 'resnet18_default.pth'))
        print("Copied default model.pth to data/models/resnet18_default.pth")
except Exception as e:
    print("Error copying default model.pth:", e)

try:
    src_pt = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'yolov8n-oiv7.pt')
    dst_pt = os.path.join(MODELS_DIR, 'yolov8n-oiv7.pt')
    if os.path.exists(src_pt) and not os.path.exists(dst_pt):
        shutil.copy2(src_pt, dst_pt)
        print("Copied yolov8n-oiv7.pt to data/models/yolov8n-oiv7.pt")
except Exception as e:
    print("Error copying yolov8n-oiv7.pt:", e)

model = None
model_classes = []
device = None
active_model_type = None  # 'resnet' or 'yolo'
active_model_name = None

def load_active_model():
    global model, model_classes, device, active_model_type, active_model_name
    settings = load_settings()
    active_model_name = settings.get('active_model', 'yolov8n-oiv7.pt')
    log_message("Loading active model: {0}".format(active_model_name))
    
    # Select device
    import torch
    if torch.backends.mps.is_available():
        device = torch.device("mps")
    elif torch.cuda.is_available():
        device = torch.device("cuda")
    else:
        device = torch.device("cpu")
        
    model_path = os.path.join(MODELS_DIR, active_model_name)
    
    if active_model_name.endswith('.pt'):
        try:
            from ultralytics import YOLO
            if not os.path.exists(model_path):
                src_pt = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'yolov8n-oiv7.pt')
                if os.path.exists(src_pt):
                    shutil.copy2(src_pt, model_path)
            model = YOLO(model_path)
            try:
                model.to(device)
            except Exception as e:
                log_message("Warning: could not move YOLO model to {0}: {1}".format(device, e))
            active_model_type = 'yolo'
            log_message("Successfully loaded YOLO model: {0}".format(active_model_name))
        except Exception as e:
            log_message("Error loading YOLO model {0}: {1}".format(active_model_name, e))
            model = None
            active_model_type = None
    else:
        if not os.path.exists(model_path):
            model_path = os.path.join(MODELS_DIR, 'resnet18_default.pth')
            active_model_name = 'resnet18_default.pth'
            
        if os.path.exists(model_path):
            try:
                import torch.nn as nn
                from torchvision import models
                
                checkpoint = torch.load(model_path, map_location=device)
                model_classes = checkpoint['classes']
                
                model = models.resnet18()
                num_ftrs = model.fc.in_features
                model.fc = nn.Linear(num_ftrs, len(model_classes))
                
                model.load_state_dict(checkpoint['model_state_dict'])
                model = model.to(device)
                model.eval()
                active_model_type = 'resnet'
                log_message("Successfully loaded trained ResNet model {0} with classes: {1}".format(active_model_name, model_classes))
            except Exception as e:
                log_message("Error loading ResNet model {0}: {1}".format(active_model_name, e))
                model = None
                active_model_type = None
        else:
            log_message("Model file {0} does not exist and no fallback found.".format(model_path))
            model = None
            active_model_type = None

def model_predict(filepath):
    global model, model_classes, device, active_model_type, active_model_name
    if model is None:
        load_active_model()
        if model is None:
            return False, 0.0
            
    try:
        if active_model_type == 'yolo':
            results = model(filepath, device=device.type if device else None, verbose=False)
            is_squirrel = False
            max_confidence = 0.0
            
            if len(results) > 0 and results[0].boxes is not None:
                for box in results[0].boxes:
                    cls_id = int(box.cls[0].item())
                    conf = float(box.conf[0].item())
                    if cls_id == 488:
                        is_squirrel = True
                        if conf > max_confidence:
                            max_confidence = conf
            return is_squirrel, max_confidence
            
        else:
            import torch
            from PIL import Image
            from torchvision import transforms
            
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

# --- Directory Paths & Env Loading ---
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

# --- Configuration ---
PI_IP = os.environ.get('PI_IP', '192.168.86.136')
PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', 'http://192.168.86.137')
DEFAULT_STREAM_URL = os.environ.get('STREAM_URL', 'http://{0}:8554/stream.mjpg'.format(PI_IP))

RAW_DIR = os.path.join(BASE_DIR, 'data', 'raw')
DATASET_DIR = os.path.join(BASE_DIR, 'data', 'dataset')
SQUIRREL_DIR = os.path.join(DATASET_DIR, 'squirrel')
NOT_SQUIRREL_DIR = os.path.join(DATASET_DIR, 'not_squirrel')
VIDEOS_DIR = os.path.join(BASE_DIR, 'data', 'videos')
TRASH_DIR = os.path.join(BASE_DIR, 'data', 'trash')
THUMBNAILS_DIR = os.path.join(BASE_DIR, 'data', 'thumbnails')
PREDICT_TMP_DIR = os.path.join(BASE_DIR, 'data', 'tmp_predict')
os.makedirs(PREDICT_TMP_DIR, exist_ok=True)

AUTOMATION_STATUS_FILE = os.path.join(BASE_DIR, 'data', 'automation_status.json')
SETTINGS_FILE = os.path.join(BASE_DIR, 'data', 'settings.json')

default_settings = {
    'capture_interval': 5,
    'analysis_interval': 5,
    'save_interval': 30,
    'daylight_mode': 'sun',
    'daylight_latitude': 38.9586,
    'daylight_longitude': -77.3570,
    'sunrise_offset_minutes': 0,
    'sunset_offset_minutes': 0,
    'daylight_start_hour': 6,
    'daylight_end_hour': 20,
    'analysis_width': 960,
    'analysis_height': 720,
    'analysis_jpeg_quality': 65,
    'review_jpeg_quality': 90,
    'motion_prefilter_enabled': True,
    'motion_threshold': 6.0,
    'motion_force_interval': 30,
    'gemini_api_key': os.environ.get('GEMINI_API_KEY', ''),
    'camera_rotation': 0,
    'camera_roi': '0.05,0.15,0.3,0.3',
    'video_roi': '0.0,0.0,0.6,0.6',
    'confidence_threshold': 0.70,
    'spray_decision_required_hits': 2,
    'spray_decision_window_seconds': 12,
    'spray_decision_average_confidence': 0.75,
    'spray_cooldown_seconds': 60,
    'notification_type': 'join',
    'public_base_url': PUBLIC_BASE_URL,
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
    'retention_days_videos': 14,
    'active_model': 'yolov8n-oiv7.pt',
    'enable_rtsp': False,
    'rtsp_stream_url': DEFAULT_STREAM_URL,
    'rtsp_motion_interval_minutes': 5
}

def setting_enabled(value):
    if isinstance(value, str):
        return value.strip().lower() in ('1', 'true', 'yes', 'on')
    return bool(value)

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                import json
                settings = json.load(f)
                merged = default_settings.copy()
                merged.update(settings)
                merged['enable_rtsp'] = setting_enabled(merged.get('enable_rtsp', True))
                if 'analysis_interval' not in settings:
                    merged['analysis_interval'] = int(merged.get('capture_interval', default_settings['analysis_interval']))
                if 'save_interval' not in settings:
                    merged['save_interval'] = 30
                return merged
        except Exception as e:
            print("Error loading settings:", e)
    settings = default_settings.copy()
    settings['enable_rtsp'] = setting_enabled(settings.get('enable_rtsp', True))
    return settings.copy()

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            import json
            json.dump(settings, f, indent=2)
    except Exception as e:
        print("Error saving settings:", e)

def make_live_frame_jpeg(img_data, settings=None):
    try:
        from io import BytesIO
        from PIL import Image

        settings = settings or load_settings()
        live_width = int(settings.get('analysis_width', default_settings['analysis_width']))
        live_height = int(settings.get('analysis_height', default_settings['analysis_height']))
        live_quality = int(settings.get('analysis_jpeg_quality', default_settings['analysis_jpeg_quality']))

        img = Image.open(BytesIO(img_data)).convert('RGB')
        if img.size != (live_width, live_height):
            img = img.resize((live_width, live_height), Image.LANCZOS)

        out = BytesIO()
        img.save(out, format='JPEG', quality=live_quality, optimize=True)
        return out.getvalue()
    except Exception as e:
        print("Error normalizing live frame:", e)
        return img_data

BLASTS_LOG_FILE = os.path.join(BASE_DIR, 'data', 'blasts_log.json')
last_spray_time = 0.0
latest_pi_status = {}
latest_predict_metrics = {}
health_history = deque(maxlen=720)
telemetry_lock = threading.Lock()
detection_history = deque()
detection_history_lock = threading.Lock()

def add_health_sample(source, pi=None, predict=None):
    pi = pi or {}
    predict = predict or {}
    now = time.time()
    with frame_lock:
        frame_age = now - latest_frame_time if latest_frame_time else None
    sample = {
        't': now,
        'source': source,
        'pi_status': pi.get('status'),
        'latest_frame_age_seconds': frame_age,
        'loop_ms': pi.get('total_ms'),
        'capture_ms': pi.get('capture_ms'),
        'upload_ms': pi.get('upload_ms'),
        'predict_total_ms': predict.get('total_ms') or pi.get('server_metrics', {}).get('total_ms'),
        'model_ms': predict.get('model_ms') or pi.get('server_metrics', {}).get('model_ms'),
        'motion_score': pi.get('motion_score'),
        'input_bytes': predict.get('input_bytes') or pi.get('file_bytes'),
        'live_bytes': predict.get('live_bytes'),
        'confidence': predict.get('confidence') if predict.get('confidence') is not None else pi.get('confidence')
    }
    health_history.append(sample)

def get_spray_decision(is_squirrel, confidence, settings, now_time=None):
    now_time = now_time or time.time()
    threshold = float(settings.get('confidence_threshold', 0.70))
    window_seconds = max(1.0, float(settings.get('spray_decision_window_seconds', 12)))
    required_hits = max(1, int(settings.get('spray_decision_required_hits', 2)))
    average_threshold = float(settings.get('spray_decision_average_confidence', threshold))

    with detection_history_lock:
        while detection_history and now_time - detection_history[0]['t'] > window_seconds:
            detection_history.popleft()

        if is_squirrel and confidence >= threshold:
            detection_history.append({'t': now_time, 'confidence': confidence})

        hits = list(detection_history)
        avg_confidence = sum(hit['confidence'] for hit in hits) / len(hits) if hits else 0.0
        ready = len(hits) >= required_hits and avg_confidence >= average_threshold

        if ready:
            detection_history.clear()

    return {
        'ready': ready,
        'hits': len(hits),
        'required_hits': required_hits,
        'window_seconds': window_seconds,
        'average_confidence': avg_confidence,
        'average_threshold': average_threshold
    }

def get_local_base_url():
    settings = load_settings()
    configured_url = str(settings.get('public_base_url') or '').strip()
    if configured_url:
        return configured_url.rstrip('/')

    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = '127.0.0.1'
    return "http://{0}:5001".format(local_ip)

def send_blast_notification(blast_type, confidence=None, image_filename=None):
    # Instead of running sync_images.sh (which fails in Docker), we poll for the newly
    # uploaded and converted video file to arrive in data/videos/ from the Pi.
    video_path = None
    video_filename = None
    
    for attempt in range(75):
        try:
            import glob
            video_files = glob.glob(os.path.join(VIDEOS_DIR, '*.mp4'))
            if video_files:
                video_files.sort(key=os.path.getmtime)
                candidate_path = video_files[-1]
                # Recording, upload, and conversion can take 30-60s on the Pi 3.
                if time.time() - os.path.getmtime(candidate_path) < 120:
                    video_path = candidate_path
                    video_filename = os.path.basename(video_path)
                    break
        except Exception:
            pass
        time.sleep(1)
        
    if not video_path:
        log_message("[Notification] Warning: No new spray video was found in VIDEOS_DIR after 75s polling.")

    settings = load_settings()
    notification_type = settings.get('notification_type', 'join')
    
    title = "🐿️ Squirrel Blasted! 💦"
    base_url = get_local_base_url()
    image_url, image_path = build_image_url(image_filename, base_url)

    if blast_type == 'auto':
        msg = "Automatic repeller triggered a water spray! (Model confidence: {0:.1f}%)".format(confidence * 100 if confidence else 0)
    else:
        msg = "Manual spray triggered from the web interface."

    if image_url:
        msg += "\n\nTrigger image: {0}".format(image_url)
        
    # Construct video URL for Join Push (Try public hosts first, fallback to local IP)
    video_url = None
    if video_path and video_filename:
        video_url = upload_video_to_public_host(video_path)
        if not video_url:
            video_url = "{0}/video/{1}".format(base_url, video_filename)
        
        # Append the public/local video URL directly to the notification message body for visibility
        msg += "\n\nWatch video: {0}".format(video_url)
        
    log_message(msg)
    
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
                elif image_url:
                    params['url'] = image_url
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
                
                if image_path and os.path.exists(image_path):
                    try:
                        with open(image_path, 'rb') as attachment:
                            part = MIMEBase('image', 'jpeg')
                            part.set_payload(attachment.read())
                            encoders.encode_base64(part)
                            part.add_header('Content-Disposition', 'attachment; filename= {0}'.format(image_filename))
                            mime_msg.attach(part)
                    except Exception as ie:
                        log_message("[Notification] Error attaching image: {0}".format(ie))

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

def log_blast(blast_type, confidence=None, model_name=None, image_filename=None, duration=None):
    import datetime
    now = datetime.datetime.now()
    if duration is None:
        duration = get_current_spray_duration()
    
    if model_name is None and blast_type == 'auto':
        model_name = active_model_name
        
    try:
        db_blast = DBBlast(
            timestamp=now,
            type=blast_type,
            confidence=confidence,
            duration=duration,
            video_filename=None,
            model_name=model_name
        )
        db_session.add(db_blast)
        db_session.commit()
        
        # Asynchronous notification dispatch
        import threading
        threading.Thread(target=send_blast_notification, args=(blast_type, confidence, image_filename)).start()
    except Exception as e:
        db_session.rollback()
        print("Error logging blast to DB:", e)

def get_current_spray_duration():
    settings = load_settings()
    std_duration = settings.get('spray_duration', 3.0)
    long_duration = settings.get('long_spray_duration', 5.0)
    threshold_hours = settings.get('long_spray_threshold_hours', 2.0)
    
    try:
        last_blast = db_session.query(DBBlast).order_by(DBBlast.timestamp.desc()).first()
        if not last_blast:
            return long_duration
            
        time_diff = datetime.datetime.now() - last_blast.timestamp
        diff_hours = time_diff.total_seconds() / 3600.0
        
        if diff_hours >= threshold_hours:
            return long_duration
        else:
            return std_duration
    except Exception as e:
        print("Error calculating spray duration from DB:", e)
        return std_duration

automation_enabled = True
training_process = None

last_exit_code = None
model_reloaded = False

def load_automation_status():
    global automation_enabled
    try:
        s = db_session.query(DBSetting).filter_by(key='automation_enabled').first()
        if s:
            automation_enabled = (s.value.lower() == 'true')
        else:
            automation_enabled = True
    except Exception as e:
        print("Error loading automation status from DB:", e)
        automation_enabled = True

def save_automation_status(enabled):
    global automation_enabled
    automation_enabled = enabled
    try:
        s = db_session.query(DBSetting).filter_by(key='automation_enabled').first()
        if s:
            s.value = str(enabled)
        else:
            db_session.add(DBSetting(key='automation_enabled', value=str(enabled)))
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        print("Error saving automation status to DB:", e)

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
                    # Delete from database
                    db_session.query(DBImage).filter_by(filename=f).delete()
                    deleted += 1
            except Exception as e:
                print("Error deleting file {0}: {1}".format(fp, e))
    if deleted > 0:
        try:
            db_session.commit()
        except Exception as e:
            db_session.rollback()
            print("Error committing database cleanup:", e)
    return deleted

def clean_videos_directory(directory, retention_days):
    if not os.path.exists(directory):
        return 0
    
    deleted = 0
    now = time.time()
    cutoff = now - (retention_days * 86400)
    for f in os.listdir(directory):
        fp = os.path.join(directory, f)
        if os.path.isfile(fp):
            try:
                db_vid = db_session.query(DBVideo).filter_by(filename=f).first()
                if db_vid and db_vid.is_favorite:
                    continue
                
                mtime = os.path.getmtime(fp)
                if mtime < cutoff:
                    deleted += delete_video_files(f)
            except Exception as e:
                print("Error cleaning video {0}: {1}".format(f, e))
    if deleted > 0:
        try:
            db_session.commit()
        except Exception as e:
            db_session.rollback()
            print("Error committing video cleanup:", e)
    return deleted

def delete_video_files(filename):
    safe_filename = os.path.basename(filename)
    if safe_filename != filename:
        raise ValueError("Invalid video filename")

    deleted = 0
    paths = [
        os.path.join(VIDEOS_DIR, safe_filename)
    ]
    if safe_filename.lower().endswith('.mp4'):
        paths.append(os.path.join(VIDEOS_DIR, safe_filename[:-4] + '.jpg'))

    for path in paths:
        if os.path.exists(path) and os.path.isfile(path):
            os.remove(path)
            deleted += 1
    return deleted

def get_blast_for_video(db_video_or_filename):
    if isinstance(db_video_or_filename, DBVideo):
        db_video = db_video_or_filename
        filename = db_video.filename
        if db_video.blast_id:
            blast = db_session.query(DBBlast).filter_by(id=db_video.blast_id).first()
            if blast:
                return blast
    else:
        filename = db_video_or_filename
        db_video = db_session.query(DBVideo).filter_by(filename=filename).first()
        if db_video and db_video.blast_id:
            blast = db_session.query(DBBlast).filter_by(id=db_video.blast_id).first()
            if blast:
                return blast

    return db_session.query(DBBlast).filter_by(video_filename=filename).first()

def get_video_event_classification(db_video):
    blast = get_blast_for_video(db_video)
    if blast and blast.classification:
        return blast.classification
    return db_video.classification

def set_video_event_classification(db_video, classification):
    db_video.classification = classification
    blast = get_blast_for_video(db_video)
    if blast:
        blast.classification = classification
        if not db_video.blast_id:
            db_video.blast_id = blast.id
        if not blast.video_filename:
            blast.video_filename = db_video.filename
    return blast

def link_video_to_blast(db_video, matched_blast):
    db_video.blast_id = matched_blast.id
    matched_blast.video_filename = db_video.filename
    if db_video.classification and not matched_blast.classification:
        matched_blast.classification = db_video.classification
    elif matched_blast.classification and not db_video.classification:
        db_video.classification = matched_blast.classification

def extract_false_alarm_training_frames(video_name, max_frames=6):
    safe_name = os.path.basename(video_name)
    if safe_name != video_name:
        raise ValueError("Invalid video filename")

    video_path = os.path.join(VIDEOS_DIR, safe_name)
    if not os.path.exists(video_path):
        return {'created': 0, 'skipped': 1, 'reason': 'missing_video'}

    try:
        import cv2
    except Exception as e:
        return {'created': 0, 'skipped': 1, 'reason': 'opencv_unavailable: {0}'.format(e)}

    os.makedirs(NOT_SQUIRREL_DIR, exist_ok=True)
    stem = os.path.splitext(safe_name)[0]
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return {'created': 0, 'skipped': 1, 'reason': 'open_failed'}

    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_count <= 0:
            return {'created': 0, 'skipped': 1, 'reason': 'empty_video'}

        sample_count = max(1, min(int(max_frames), frame_count))
        if sample_count == 1:
            frame_indices = [frame_count // 2]
        else:
            frame_indices = [
                int(round((frame_count - 1) * (idx + 1) / (sample_count + 1)))
                for idx in range(sample_count)
            ]

        created = 0
        for idx, frame_idx in enumerate(frame_indices):
            out_name = "false_alarm_{0}_{1:02d}.jpg".format(stem, idx + 1)
            out_path = os.path.join(NOT_SQUIRREL_DIR, out_name)
            if os.path.exists(out_path):
                continue

            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue

            if not cv2.imwrite(out_path, frame):
                continue

            captured_at = get_video_timestamp(safe_name) or datetime.datetime.now()
            try:
                db_img = db_session.query(DBImage).filter_by(filename=out_name).first()
                if not db_img:
                    db_session.add(DBImage(
                        filename=out_name,
                        category='not_squirrel',
                        captured_at=captured_at,
                        prediction_confidence=None,
                        is_auto_classified=True
                    ))
                created += 1
            except Exception as e:
                db_session.rollback()
                log_message("[False Alarm Training] DB insert failed for {0}: {1}".format(out_name, e))
        if created > 0:
            db_session.commit()
        return {'created': created, 'skipped': 0, 'reason': None}
    finally:
        cap.release()

def extract_all_false_alarm_training_frames(max_frames=6):
    videos = [
        video for video in db_session.query(DBVideo).all()
        if get_video_event_classification(video) == 'false_positive'
    ]
    total_created = 0
    missing = 0
    processed = 0
    for video in videos:
        result = extract_false_alarm_training_frames(video.filename, max_frames=max_frames)
        total_created += result.get('created', 0)
        if result.get('reason') == 'missing_video':
            missing += 1
        else:
            processed += 1
    return {
        'processed': processed,
        'missing': missing,
        'created': total_created,
        'total_false_alarms': len(videos)
    }

def clean_not_squirrel_directory(not_squirrel_dir, retention_days, min_count):
    if not os.path.exists(not_squirrel_dir):
        return 0
        
    try:
        db_imgs = db_session.query(DBImage).filter_by(category='not_squirrel').order_by(DBImage.captured_at.desc()).all()
    except Exception as e:
        print("Error fetching not_squirrel images from DB for cleanup:", e)
        return 0
        
    now = datetime.datetime.now()
    cutoff = now - datetime.timedelta(days=retention_days)
    deleted = 0
    
    for idx, db_img in enumerate(db_imgs):
        if idx < min_count:
            continue
        if db_img.captured_at < cutoff:
            fp = os.path.join(not_squirrel_dir, db_img.filename)
            try:
                if os.path.exists(fp):
                    os.remove(fp)
                db_session.delete(db_img)
                deleted += 1
            except Exception as e:
                print("Error deleting old not_squirrel image {0}: {1}".format(fp, e))
                
    if deleted > 0:
        try:
            db_session.commit()
        except Exception as e:
            db_session.rollback()
            print("Error committing not_squirrel cleanup:", e)
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
for d in [RAW_DIR, SQUIRREL_DIR, NOT_SQUIRREL_DIR, VIDEOS_DIR, TRASH_DIR, THUMBNAILS_DIR]:
    os.makedirs(d, exist_ok=True)

# Keep track of classification history for undo (in-memory stack fallback)
classification_history = []

def get_image_timestamp(filename):
    import re
    import datetime
    match = re.match(r'img_(?:auto_)?(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(?:_\d+)?\.(?:jpg|jpeg|png)', filename, re.IGNORECASE)
    if match:
        parts = [int(p) for p in match.groups()]
        try:
            return datetime.datetime(parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])
        except Exception:
            return None
    return None

def sync_db_with_filesystem():
    """Synchronizes the SQLite database with the files currently on disk."""
    try:
        disk_images = {}
        category_map = {
            'raw': RAW_DIR,
            'squirrel': SQUIRREL_DIR,
            'not_squirrel': NOT_SQUIRREL_DIR,
            'trash': TRASH_DIR
        }
        for cat, directory in category_map.items():
            if os.path.exists(directory):
                for f in os.listdir(directory):
                    if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                        disk_images[f] = cat
                        
        db_imgs = db_session.query(DBImage).all()
        db_images_map = {img.filename: img for img in db_imgs}
        
        # 1. Update/insert images from disk
        for filename, disk_cat in disk_images.items():
            if filename in db_images_map:
                db_img = db_images_map[filename]
                if db_img.category != disk_cat:
                    db_img.category = disk_cat
            else:
                fp = os.path.join(category_map[disk_cat], filename)
                mtime = os.path.getmtime(fp)
                captured_at = get_image_timestamp(filename)
                if not captured_at:
                    captured_at = datetime.datetime.fromtimestamp(mtime)
                new_img = DBImage(
                    filename=filename,
                    category=disk_cat,
                    captured_at=captured_at,
                    prediction_confidence=None,
                    is_auto_classified=filename.startswith('img_auto_')
                )
                db_session.add(new_img)
                
        # 2. Remove images from DB that no longer exist on disk
        for filename, db_img in db_images_map.items():
            if filename not in disk_images:
                db_session.delete(db_img)
                
        db_session.commit()
        print("Database filesystem sync complete.")
    except Exception as e:
        db_session.rollback()
        print("Error during database filesystem sync:", e)

def init_db_and_migrate():
    # 1. Create tables
    Base.metadata.create_all(bind=engine)
    
    # 1.5 Migrate event/media columns if needed.
    try:
        result = db_session.execute(text("PRAGMA table_info(blasts)")).fetchall()
        columns = [row[1] for row in result]
        if 'model_name' not in columns:
            log_message("[Migration] Adding model_name column to blasts table.")
            db_session.execute(text("ALTER TABLE blasts ADD COLUMN model_name VARCHAR"))
            db_session.commit()
        if 'classification' not in columns:
            log_message("[Migration] Adding classification column to blasts table.")
            db_session.execute(text("ALTER TABLE blasts ADD COLUMN classification VARCHAR"))
            db_session.commit()
    except Exception as e:
        db_session.rollback()
        log_message("[Migration] Error adding event columns to blasts: {0}".format(e))

    try:
        result = db_session.execute(text("PRAGMA table_info(videos)")).fetchall()
        columns = [row[1] for row in result]
        if 'blast_id' not in columns:
            log_message("[Migration] Adding blast_id column to videos table.")
            db_session.execute(text("ALTER TABLE videos ADD COLUMN blast_id INTEGER"))
            db_session.commit()
    except Exception as e:
        db_session.rollback()
        log_message("[Migration] Error adding blast_id column to videos: {0}".format(e))

    try:
        videos = db_session.query(DBVideo).all()
        for video in videos:
            blast = get_blast_for_video(video)
            if not blast:
                continue
            if not video.blast_id:
                video.blast_id = blast.id
            if not blast.video_filename:
                blast.video_filename = video.filename
            if video.classification and not blast.classification:
                blast.classification = video.classification
            elif blast.classification and not video.classification:
                video.classification = blast.classification
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        log_message("[Migration] Error backfilling event/media links: {0}".format(e))
    
    # 2. Check if we need to migrate settings
    try:
        import json
        settings_migrated = False
        if db_session.query(DBSetting).count() == 0:
            if os.path.exists(SETTINGS_FILE):
                try:
                    with open(SETTINGS_FILE, 'r') as f:
                        settings = json.load(f)
                    save_settings(settings)
                    settings_migrated = True
                    log_message("[Migration] Migrated settings from JSON to SQLite database.")
                except Exception as e:
                    log_message("[Migration] Error migrating settings.json: {0}".format(e))
            else:
                save_settings(default_settings)
                log_message("[Migration] Initialized default settings in SQLite database.")
                
            if os.path.exists(AUTOMATION_STATUS_FILE):
                try:
                    with open(AUTOMATION_STATUS_FILE, 'r') as f:
                        status = json.load(f)
                    enabled = status.get('enabled', True)
                    save_automation_status(enabled)
                    log_message("[Migration] Migrated automation status: {0}".format(enabled))
                except Exception as e:
                    log_message("[Migration] Error migrating automation_status.json: {0}".format(e))
            else:
                save_automation_status(True)
                
            if settings_migrated and os.path.exists(SETTINGS_FILE):
                try:
                    shutil.move(SETTINGS_FILE, SETTINGS_FILE + '.bak')
                except Exception as e:
                    log_message("[Migration] Error backing up settings.json: {0}".format(e))
            if os.path.exists(AUTOMATION_STATUS_FILE):
                try:
                    shutil.move(AUTOMATION_STATUS_FILE, AUTOMATION_STATUS_FILE + '.bak')
                except Exception as e:
                    log_message("[Migration] Error backing up automation_status.json: {0}".format(e))
    except Exception as e:
        log_message("[Migration] Settings migration outer block failed: {0}".format(e))

    # 3. Check if we need to migrate blasts and videos
    try:
        if db_session.query(DBBlast).count() == 0:
            if os.path.exists(BLASTS_LOG_FILE):
                try:
                    with open(BLASTS_LOG_FILE, 'r') as f:
                        blasts_data = json.load(f)
                    if not isinstance(blasts_data, list):
                        blasts_data = []
                    
                    log_message("[Migration] Found {0} blasts to migrate.".format(len(blasts_data)))
                    
                    video_files = []
                    if os.path.exists(VIDEOS_DIR):
                        video_files = [f for f in os.listdir(VIDEOS_DIR) if f.lower().endswith('.mp4')]
                    
                    videos_to_insert = {}
                    
                    for entry in blasts_data:
                        entry_time_str = entry.get('timestamp')
                        if not entry_time_str:
                            continue
                        try:
                            entry_time = datetime.datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            continue
                            
                        matching_video = None
                        for vf in video_files:
                            v_time = get_video_timestamp(vf)
                            if v_time and abs((v_time - entry_time).total_seconds()) < 6.0:
                                matching_video = vf
                                break
                                
                        blast = DBBlast(
                            timestamp=entry_time,
                            type=entry.get('type', 'auto'),
                            confidence=entry.get('confidence'),
                            duration=entry.get('duration', default_settings['spray_duration']),
                            video_filename=matching_video,
                            classification=entry.get('classification')
                        )
                        db_session.add(blast)
                        db_session.flush()
                        
                        if matching_video:
                            videos_to_insert[matching_video] = {
                                'filename': matching_video,
                                'blast_id': blast.id,
                                'is_favorite': entry.get('favorite', False),
                                'classification': entry.get('classification'),
                                'created_at': get_video_timestamp(matching_video) or entry_time
                            }
                    
                    for filename, v_info in videos_to_insert.items():
                        db_vid = DBVideo(
                            filename=v_info['filename'],
                            blast_id=v_info.get('blast_id'),
                            is_favorite=v_info['is_favorite'],
                            classification=v_info['classification'],
                            created_at=v_info['created_at']
                        )
                        db_session.add(db_vid)
                        
                    db_session.commit()
                    log_message("[Migration] Successfully migrated blasts log to SQLite DB.")
                    
                    try:
                        shutil.move(BLASTS_LOG_FILE, BLASTS_LOG_FILE + '.bak')
                    except Exception as e:
                        log_message("[Migration] Error backing up blasts_log.json: {0}".format(e))
                except Exception as e:
                    db_session.rollback()
                    log_message("[Migration] Error migrating blasts log: {0}".format(e))
    except Exception as e:
        log_message("[Migration] Blasts migration outer block failed: {0}".format(e))

    # 4. Sync images with filesystem
    sync_db_with_filesystem()

def get_stats():
    """Returns the count of images in each category from the DB."""
    try:
        raw_count = db_session.query(DBImage).filter_by(category='raw').count()
        squirrel_count = db_session.query(DBImage).filter_by(category='squirrel').count()
        not_squirrel_count = db_session.query(DBImage).filter_by(category='not_squirrel').count()
        
        latest_img = db_session.query(DBImage).order_by(DBImage.captured_at.desc()).first()
        latest_mtime = latest_img.captured_at.timestamp() if latest_img else 0
        with frame_lock:
            if latest_frame_time and latest_frame_time > latest_mtime:
                latest_mtime = latest_frame_time
    except Exception as e:
        print("Error getting stats from DB:", e)
        raw_count = 0
        squirrel_count = 0
        not_squirrel_count = 0
        latest_mtime = 0
        
    import datetime
    current_hour = datetime.datetime.now().hour

    settings = load_settings()
    enable_rtsp = settings.get('enable_rtsp', True)

    return {
        'raw_count': raw_count,
        'squirrel_count': squirrel_count,
        'not_squirrel_count': not_squirrel_count,
        'total_dataset_count': squirrel_count + not_squirrel_count,
        'latest_image_mtime': latest_mtime,
        'current_hour': current_hour,
        'enable_rtsp': enable_rtsp
    }

def get_next_raw_image():
    """Returns the filename of the next raw image to classify from the DB."""
    try:
        img = db_session.query(DBImage).filter_by(category='raw').order_by(DBImage.filename.asc()).first()
        if img:
            return img.filename
    except Exception as e:
        print("Error getting next raw image from DB:", e)
    return None

video_processing_lock = threading.Lock()
sync_lock = threading.Lock()

def process_synced_videos():
    """Finds all .h264 files in RAW_DIR, converts them to .mp4 in VIDEOS_DIR, generates thumbnails, and deletes source files."""
    with video_processing_lock:
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
                temp_mp4_path = mp4_path + '.tmp'
                temp_thumb_path = None
                
                cmd = [ffmpeg_path, '-y', '-i', h264_path, '-c:v', 'copy', '-f', 'mp4', temp_mp4_path]
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    os.rename(temp_mp4_path, mp4_path)
                    os.remove(h264_path)
                    print("Successfully converted {0} to {1}".format(filename, mp4_filename))
                    
                    thumb_filename = os.path.splitext(mp4_filename)[0] + '.jpg'
                    thumb_path = os.path.join(VIDEOS_DIR, thumb_filename)
                    temp_thumb_path = thumb_path + '.tmp'
                    thumb_cmd = [ffmpeg_path, '-y', '-i', mp4_path, '-ss', '00:00:00.5', '-vframes', '1', '-f', 'image2', temp_thumb_path]
                    subprocess.run(thumb_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    os.rename(temp_thumb_path, thumb_path)
                    print("Generated thumbnail for {0}".format(mp4_filename))
                except Exception as e:
                    print("Error processing video {0}: {1}".format(filename, str(e)))
                    for path in [temp_mp4_path, temp_thumb_path]:
                        if path and os.path.exists(path):
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                
    try:
        for filename in os.listdir(VIDEOS_DIR):
            if filename.startswith('compilation_'):
                continue
            if filename.lower().endswith('.mp4'):
                db_vid = db_session.query(DBVideo).filter_by(filename=filename).first()
                video_time = get_video_timestamp(filename)
                
                if not db_vid:
                    created_at = video_time if video_time else datetime.datetime.now()
                    db_vid = DBVideo(
                        filename=filename,
                        blast_id=None,
                        is_favorite=False,
                        classification=None,
                        created_at=created_at
                    )
                    db_session.add(db_vid)
                    db_session.flush()
                
                if video_time:
                    # Query candidate blasts within +/- 14 hours to allow timezone offset matching
                    candidates = db_session.query(DBBlast).filter(
                        DBBlast.timestamp.between(
                            video_time - datetime.timedelta(hours=14),
                            video_time + datetime.timedelta(hours=14)
                        )
                    ).filter(
                        (DBBlast.video_filename == None) | (DBBlast.video_filename == filename) | (DBBlast.id == db_vid.blast_id)
                    ).order_by(DBBlast.timestamp.asc()).all()
                    
                    matched_blast = None
                    min_error = 999.0
                    for b_candidate in candidates:
                        diff_seconds = (b_candidate.timestamp - video_time).total_seconds()
                        # Timezone offsets are multiples of 15 minutes (900 seconds)
                        closest_offset = round(diff_seconds / 900.0) * 900.0
                        error = abs(diff_seconds - closest_offset)
                        if error <= 6.0 and error < min_error:
                            matched_blast = b_candidate
                            min_error = error
                            
                    if matched_blast:
                        link_video_to_blast(db_vid, matched_blast)
                        # Heal the timestamp: align the database record to the local Eastern Time from the filename
                        matched_blast.timestamp = video_time
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        print("Error syncing video files with DB:", e)
        
    for filename in os.listdir(VIDEOS_DIR):
        if filename.startswith('compilation_'):
            continue
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

# Initial DB migration and conversion on startup
init_db_and_migrate()
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

        pre, code, input, textarea {
            user-select: text !important;
            -webkit-user-select: text !important;
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

        .btn:disabled {
            opacity: 0.4;
            cursor: not-allowed;
            pointer-events: none;
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

        .confidence-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.35rem;
            margin-top: 0.45rem;
            padding: 0.28rem 0.55rem;
            border-radius: 8px;
            border: 1px solid rgba(34, 211, 238, 0.35);
            background: rgba(34, 211, 238, 0.1);
            color: #bae6fd;
            font-family: 'Outfit', sans-serif;
            font-size: 0.75rem;
            line-height: 1.1;
            white-space: nowrap;
        }

        .confidence-badge span {
            color: var(--text-secondary);
            text-transform: uppercase;
            font-size: 0.62rem;
            letter-spacing: 0;
        }

        .confidence-badge strong {
            color: #e0f2fe;
            font-weight: 700;
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
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 0.35rem;
        }

        .gallery-card-info .confidence-badge {
            margin-top: 0;
        }

        .gallery-filename {
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
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
        .health-layout {
            display: grid;
            grid-template-columns: minmax(260px, 0.8fr) minmax(0, 1.6fr);
            gap: 1.25rem;
            align-items: stretch;
            min-height: 260px;
        }
        .health-metrics-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.75rem;
            align-content: start;
            font-size: 0.85rem;
        }
        .health-chart-wrap {
            min-height: 260px;
            position: relative;
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
            .health-layout {
                grid-template-columns: 1fr;
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

        .mobile-nav-tabs {
            display: none;
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
            .mobile-nav-tabs {
                display: flex;
                overflow-x: auto;
                white-space: nowrap;
                background: rgba(15, 23, 42, 0.85);
                border-bottom: 1px solid var(--border-color);
                padding: 0.6rem 1rem;
                gap: 0.5rem;
                position: sticky;
                top: 0;
                z-index: 100;
                backdrop-filter: blur(10px);
                -webkit-overflow-scrolling: touch;
            }
            .mobile-nav-tabs::-webkit-scrollbar {
                display: none;
            }
            .mobile-nav-tabs {
                -ms-overflow-style: none;
                scrollbar-width: none;
            }
            .nav-tab {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid var(--border-color);
                color: var(--text-secondary);
                padding: 0.4rem 0.9rem;
                border-radius: 20px;
                font-size: 0.85rem;
                font-family: Outfit, sans-serif;
                font-weight: 500;
                cursor: pointer;
                transition: all 0.2s ease;
                display: inline-flex;
                align-items: center;
                gap: 0.3rem;
            }
            .nav-tab.active {
                background: var(--color-sync);
                color: white;
                border-color: var(--color-sync);
                box-shadow: 0 2px 8px rgba(59, 130, 246, 0.4);
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

        /* --- Responsive Video Modal Actions & Scrollable Modals --- */
        .modal {
            overflow-y: auto;
            padding: 2rem 0;
        }

        @media (max-height: 750px) {
            .modal {
                align-items: flex-start;
            }
        }

        .video-modal-actions {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 0.75rem;
            margin-top: 1rem;
            margin-bottom: 0.5rem;
            flex-wrap: wrap;
            width: 100%;
        }

        .video-modal-actions .btn {
            padding: 0.5rem 1.25rem;
            font-weight: 600;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            background-color: transparent;
            color: var(--text-secondary);
            cursor: pointer;
            transition: all 0.15s ease;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }

        .video-modal-actions .btn:hover {
            border-color: rgba(255, 255, 255, 0.2);
            color: var(--text-primary);
        }

        .video-modal-actions .btn.favorite-active {
            border-color: #f59e0b;
            background-color: rgba(245, 158, 11, 0.2);
            color: #f59e0b;
        }

        .video-modal-actions .btn.accurate-active {
            border-color: var(--color-squirrel);
            background-color: rgba(16, 185, 129, 0.2);
            color: var(--color-squirrel);
        }

        .video-modal-actions .btn.false-positive-active {
            border-color: var(--color-not-squirrel);
            background-color: rgba(239, 68, 68, 0.2);
            color: var(--color-not-squirrel);
        }

        .video-modal-actions .separator {
            width: 1px;
            height: 24px;
            background: rgba(255, 255, 255, 0.1);
            margin: 0 0.5rem;
            align-self: center;
        }

        @media (max-width: 768px) {
            .video-modal-actions {
                gap: 0.5rem;
            }
            .video-modal-actions .btn {
                padding: 0.4rem 0.8rem;
                font-size: 0.8rem;
                flex: 1 1 auto;
                min-width: 105px;
            }
            .video-modal-actions .separator {
                display: none;
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

    <div class="mobile-nav-tabs">
        <button id="mob-tab-dashboard" class="nav-tab active" onclick="setViewMode('dashboard')">Dashboard 📊</button>
        <button id="mob-tab-videos" class="nav-tab" onclick="setViewMode('videos')">Videos 📹</button>
        <button id="mob-tab-queue" class="nav-tab" onclick="setViewMode('queue')">Queue 📥</button>
        <button id="mob-tab-squirrel" class="nav-tab" onclick="setViewMode('squirrel')">Squirrels 🐿️</button>
        <button id="mob-tab-not_squirrel" class="nav-tab" onclick="setViewMode('not_squirrel')">Not Squirrels ❌</button>
        <button id="mob-tab-train" class="nav-tab" onclick="setViewMode('train')">Train 🧠</button>
        <button id="mob-tab-settings" class="nav-tab" onclick="setViewMode('settings')">Settings ⚙️</button>
        <button id="mob-tab-logs" class="nav-tab" onclick="setViewMode('logs')">Logs 📋</button>
    </div>

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
                        <button id="mode-videos" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('videos')">Spray Videos 📹</button>
                        <button id="mode-queue" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('queue')">Classify Queue 📥</button>
                        <button id="mode-squirrel" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('squirrel')">Review Squirrels</button>
                        <button id="mode-not_squirrel" class="btn" style="justify-content: center; background-color: transparent; border: 1px solid var(--border-color); color: var(--text-secondary);" onclick="setViewMode('not_squirrel')">Review Not Squirrels</button>
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
            <div id="compilation-header" style="display: none; width: 100%; justify-content: space-between; align-items: center; margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--border-color);">
                <span id="compilation-header-info" style="font-weight: 600; font-size: 1rem; color: var(--text-primary);"></span>
                <span style="font-size: 0.8rem; color: var(--text-secondary); background: rgba(255,255,255,0.05); padding: 0.25rem 0.5rem; border-radius: 6px; border: 1px solid var(--border-color);">Compilation Mode 🍿</span>
            </div>
            <div style="width: 100%; height: 480px; max-height: 60vh; background: #020617; border-radius: 12px; overflow: hidden; display: flex; align-items: center; justify-content: center; border: 1px solid var(--border-color);">
                <video id="modal-video-element" controls autoplay loop style="max-width: 100%; max-height: 100%; object-fit: contain;"></video>
            </div>
            <div id="modal-video-filename" class="image-filename" style="margin-top: 1rem; text-align: center;"></div>
            <div id="modal-video-classification-actions" class="video-modal-actions"></div>
        </div>
    </div>

    <!-- --- Simple Preview Modal --- -->
    <div id="preview-modal" class="modal" onclick="closePreviewModal()">
        <div class="modal-content" onclick="event.stopPropagation()" style="max-width: 90vw; width: auto; max-height: 90vh; position: relative;">
            <span class="close-btn" onclick="closePreviewModal()">&times;</span>
            <div style="display: flex; justify-content: center; align-items: center; max-height: 80vh; overflow: hidden; position: relative; border-radius: 8px;">
                <img id="preview-modal-img" src="" style="max-width: 100%; max-height: 80vh; object-fit: contain; border-radius: 8px;">
                <div id="preview-modal-time" style="position: absolute; bottom: 12px; right: 12px; background: rgba(15, 23, 42, 0.85); color: white; padding: 0.35rem 0.65rem; border-radius: 8px; font-size: 0.8rem; font-family: monospace; border: 1px solid var(--border-color); backdrop-filter: blur(6px); pointer-events: none;">Captured: --</div>
            </div>
            <div id="preview-modal-title" style="margin-top: 1rem; font-weight: 600; text-align: center; color: var(--text-primary);">Live Snapshot Preview</div>
            <div style="display: flex; justify-content: center; margin-top: 1rem; margin-bottom: 0.5rem;">
                <button id="modal-spray-btn" class="btn" style="background-color: var(--color-not-squirrel); color: white; box-shadow: 0 4px 12px rgba(239, 68, 68, 0.3); padding: 0.75rem 2rem; font-size: 1.1rem; border-radius: 12px; display: flex; align-items: center; gap: 0.5rem; justify-content: center;" onclick="triggerSpray()">
                    <span class="spinner" id="modal-spray-spinner" style="border-left-color: white; display: none;"></span>
                    <span class="btn-text" id="modal-spray-text">Spray 💦</span>
                </button>
            </div>
        </div>
    </div>

    <script>
        let currentImage = null; // Used in queue mode
        let currentImageConfidence = null;
        let viewMode = 'dashboard';
        let reverseOrder = true;
        let rtspEnabled = false;
        let currentIndex = 0; // For queue mode
        let totalCount = 0; // For queue mode

        // Gallery & Video variables
        let galleryImages = []; // List of images on the current page
        let galleryImageMeta = {};
        let currentPage = 1;
        let totalPages = 1;
        let galleryTotalCount = 0;
        let modalIndex = 0; // Index of the currently open image in the galleryImages array
        let videoClassifications = {};
        let videoFavorites = {};
        let showFavoritesOnly = false;
        let videoCurrentPage = 1;

        // Compilation Playlist Variables
        let allLoadedVideos = [];

        function toggleFavoritesFilter() {
            showFavoritesOnly = !showFavoritesOnly;
            videoCurrentPage = 1;
            loadNext();
        }

        function toggleReverse(checked) {
            reverseOrder = checked;
            // Reset to page 1 on sorting change
            currentPage = 1;
            videoCurrentPage = 1;
            loadNext();
        }

        async function setPage(page) {
            if (page < 1 || page > totalPages) return;
            currentPage = page;
            await loadNext();
            const header = document.querySelector('#workspace-card h2');
            if (header) {
                header.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else {
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        }

        async function setVideoPage(page) {
            videoCurrentPage = page;
            await loadNext();
            const header = document.querySelector('#workspace-card h2');
            if (header) {
                header.scrollIntoView({ behavior: 'smooth', block: 'start' });
            } else {
                window.scrollTo({ top: 0, behavior: 'smooth' });
            }
        }

        function navigatePage(delta) {
            if (viewMode === 'queue') {
                let targetIdx = currentIndex + delta;
                if (targetIdx < 0) targetIdx = 0;
                if (targetIdx >= totalCount) targetIdx = totalCount - 1;
                loadNext('', targetIdx);
            }
        }

        function formatConfidence(value) {
            if (value === undefined || value === null || value === '') return 'Confidence: n/a';
            const num = Number(value);
            if (Number.isNaN(num)) return 'Confidence: n/a';
            return `Confidence: ${(num * 100).toFixed(1)}%`;
        }

        function confidenceBadge(value, compact = false) {
            return `
                <div class="confidence-badge" title="Classifier confidence for the saved decision">
                    ${compact ? '' : '<span>Model</span>'}
                    <strong>${formatConfidence(value).replace('Confidence: ', '')}</strong>
                </div>
            `;
        }

        function setViewMode(mode) {
            viewMode = mode;
            currentPage = 1; // Reset page
            videoCurrentPage = 1; // Reset video page
            if (mode !== 'videos') {
                showFavoritesOnly = false;
            }
            
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
            
            const modes = ['dashboard', 'videos', 'queue', 'squirrel', 'not_squirrel', 'train', 'settings', 'logs'];
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
                
                const mobTab = document.getElementById(`mob-tab-${m}`);
                if (mobTab) {
                    if (m === mode) {
                        mobTab.classList.add('active');
                        mobTab.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
                    } else {
                        mobTab.classList.remove('active');
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
        let healthChart = null;

        async function renderDashboardView() {
            const workspace = document.getElementById('workspace-card');
            if (healthChart) {
                healthChart.destroy();
                healthChart = null;
            }
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
                            <img id="dash-feed-img" src="/api/latest_image?t=${Date.now()}" onerror="this.src='https://images.unsplash.com/photo-1542273917363-3b1817f69a2d?auto=format&fit=crop&w=800&q=80'; console.warn('No camera snap available');" style="cursor: pointer;" onclick="openPreviewModal(this.src, 'Latest Camera Snapshot')">
                            <div id="dash-feed-time" style="position: absolute; bottom: 8px; right: 8px; background: rgba(15, 23, 42, 0.75); color: var(--text-primary); padding: 0.25rem 0.5rem; border-radius: 6px; font-size: 0.75rem; font-family: monospace; border: 1px solid var(--border-color); backdrop-filter: blur(4px);">Captured: --</div>
                        </div>
                        <div id="dash-feed-subtext" style="margin-top: 1rem; font-size: 0.8rem; color: var(--text-secondary); line-height: 1.4; text-align: center;">
                            Updates automatically every 5s from the latest analyzed still frame.
                        </div>
                    </div>
                </div>

                <div class="dash-panel" style="min-height: 0; margin-top: 1.5rem;">
                    <div class="dash-panel-title">
                        <span>System Health</span>
                        <span id="health-status-pill" style="font-size: 0.8rem; color: var(--text-secondary); font-weight: normal;">Checking...</span>
                    </div>
                    <div class="health-layout">
                        <div id="health-metrics-grid" class="health-metrics-grid"></div>
                        <div class="health-chart-wrap">
                            <canvas id="health-chart"></canvas>
                        </div>
                    </div>
                </div>
            `;
            await updateDashboardData();
        }

        async function refreshDashboardSnapshot() {
            if (rtspEnabled) return;
            const img = document.getElementById('dash-feed-img');
            const now = Date.now();
            const src = `/api/latest_image?t=${now}`;
            if (img) {
                img.src = src;
            }
            const previewModal = document.getElementById('preview-modal');
            const previewImg = document.getElementById('preview-modal-img');
            if (previewModal && previewModal.classList.contains('show') && previewImg) {
                previewImg.src = src;
            }
            try {
                const statsRes = await fetch('/api/next_image?mode=queue');
                const statsData = await statsRes.json();
                if (statsData.stats) {
                    updateLiveSnapshotTimestamp(statsData.stats);
                }
            } catch (e) {
                console.error("Error updating live snapshot timestamp:", e);
            }
        }

        function updateLiveSnapshotTimestamp(stats) {
            const mtime = stats.latest_image_mtime * 1000;
            let formattedTime = "Unknown";
            if (mtime > 0) {
                const date = new Date(mtime);
                const year = date.getFullYear();
                const month = String(date.getMonth() + 1).padStart(2, '0');
                const day = String(date.getDate()).padStart(2, '0');
                const hours = String(date.getHours()).padStart(2, '0');
                const minutes = String(date.getMinutes()).padStart(2, '0');
                const seconds = String(date.getSeconds()).padStart(2, '0');
                formattedTime = `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
            }

            const timeEl = document.getElementById('dash-feed-time');
            if (timeEl) timeEl.innerText = `Captured: ${formattedTime}`;

            const modalTimeEl = document.getElementById('preview-modal-time');
            if (modalTimeEl) modalTimeEl.innerText = `Captured: ${formattedTime}`;

            return mtime;
        }

        function fmtMs(value) {
            return value === undefined || value === null ? '--' : `${Number(value).toFixed(0)} ms`;
        }

        function fmtSeconds(value) {
            return value === undefined || value === null ? '--' : `${Number(value).toFixed(1)}s`;
        }

        function fmtBytes(value) {
            if (!value) return '--';
            if (value >= 1048576) return `${(value / 1048576).toFixed(2)} MB`;
            return `${(value / 1024).toFixed(0)} KB`;
        }

        function metricTile(label, value) {
            return `<div style="background: rgba(15, 23, 42, 0.45); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.7rem;">
                <div style="color: var(--text-secondary); font-size: 0.72rem; text-transform: uppercase;">${label}</div>
                <div style="color: var(--text-primary); font-weight: 600; margin-top: 0.25rem;">${value}</div>
            </div>`;
        }

        function applyRoiBox(roiString) {
            const box = document.getElementById('settings-calibration-roi-box');
            if (!box || !roiString) {
                if (box) box.style.display = 'none';
                return;
            }
            const parts = roiString.split(',').map(v => parseFloat(v.trim()));
            if (parts.length !== 4 || parts.some(v => Number.isNaN(v))) {
                box.style.display = 'none';
                return;
            }
            const [x, y, w, h] = parts;
            box.style.display = 'block';
            box.style.left = `${Math.max(0, Math.min(1, x)) * 100}%`;
            box.style.top = `${Math.max(0, Math.min(1, y)) * 100}%`;
            box.style.width = `${Math.max(0, Math.min(1, w)) * 100}%`;
            box.style.height = `${Math.max(0, Math.min(1, h)) * 100}%`;
        }

        function updateSettingsCalibration(settings = null) {
            const roiInput = document.getElementById('settings-roi');
            const roi = roiInput ? roiInput.value.trim() : (settings && settings.camera_roi) || '';
            const img = document.getElementById('settings-calibration-img');
            const details = document.getElementById('settings-calibration-details');

            if (img) img.src = `/api/latest_image?t=${Date.now()}`;
            applyRoiBox(roi);

            if (details) {
                const analysisWidth = settings ? settings.analysis_width : document.getElementById('settings-analysis-width')?.value;
                const analysisHeight = settings ? settings.analysis_height : document.getElementById('settings-analysis-height')?.value;
                const quality = settings ? settings.analysis_jpeg_quality : document.getElementById('settings-analysis-quality')?.value;
                details.innerHTML = `Still ROI: ${roi || 'off'}<br>The latest captured output is already zoomed by raspistill. The green box is drawn on the full-frame map to show the source region being cropped before capture.<br>Live output: ${analysisWidth || '--'}x${analysisHeight || '--'} q${quality || '--'}`;
            }
        }

        async function updateHealthPanel() {
            try {
                const res = await fetch('/api/health');
                const health = await res.json();
                const pi = health.pi || {};
                const predict = health.predict || {};
                const settings = health.settings || {};
                const pill = document.getElementById('health-status-pill');
                const grid = document.getElementById('health-metrics-grid');

                if (pill) {
                    const label = health.status === 'ok' ? 'OK' : health.status.toUpperCase();
                    const color = health.status === 'ok' ? '#34d399' : '#f87171';
                    pill.innerText = label;
                    pill.style.color = color;
                }
                if (grid) {
                    grid.innerHTML = [
                        metricTile('Last Frame Age', fmtSeconds(health.latest_frame_age_seconds)),
                        metricTile('Pi Loop', fmtMs(pi.total_ms)),
                        metricTile('Capture', fmtMs(pi.capture_ms)),
                        metricTile('Upload', fmtMs(pi.upload_ms)),
                        metricTile('Model', fmtMs(predict.model_ms)),
                        metricTile('Predict Total', fmtMs(predict.total_ms)),
                        metricTile('Image Size', fmtBytes(pi.file_bytes || predict.input_bytes)),
                        metricTile('Motion', pi.motion_score === undefined || pi.motion_score === null ? '--' : `${Number(pi.motion_score).toFixed(2)} / ${settings.motion_threshold}`)
                    ].join('');
                }
                await renderHealthChart();
            } catch (e) {
                console.error("Error updating health panel:", e);
            }
        }

        async function renderHealthChart() {
            const canvas = document.getElementById('health-chart');
            if (!canvas) return;

            try {
                const res = await fetch('/api/health/history?seconds=600');
                const data = await res.json();
                const samples = data.samples || [];
                const labels = samples.map(s => {
                    const d = new Date(s.t * 1000);
                    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                });

                const series = (key) => samples.map(s => s[key] === undefined || s[key] === null ? null : Number(s[key]));

                const chartData = {
                    labels,
                    datasets: [
                        {
                            label: 'Pi Loop',
                            data: series('loop_ms'),
                            borderColor: '#60a5fa',
                            backgroundColor: 'rgba(96, 165, 250, 0.12)',
                            tension: 0.25,
                            spanGaps: true,
                            yAxisID: 'y'
                        },
                        {
                            label: 'Upload',
                            data: series('upload_ms'),
                            borderColor: '#34d399',
                            backgroundColor: 'rgba(52, 211, 153, 0.12)',
                            tension: 0.25,
                            spanGaps: true,
                            yAxisID: 'y'
                        },
                        {
                            label: 'Predict',
                            data: series('predict_total_ms'),
                            borderColor: '#f59e0b',
                            backgroundColor: 'rgba(245, 158, 11, 0.12)',
                            tension: 0.25,
                            spanGaps: true,
                            yAxisID: 'y'
                        },
                        {
                            label: 'Model',
                            data: series('model_ms'),
                            borderColor: '#f87171',
                            backgroundColor: 'rgba(248, 113, 113, 0.12)',
                            tension: 0.25,
                            spanGaps: true,
                            yAxisID: 'y'
                        },
                        {
                            label: 'Motion',
                            data: series('motion_score'),
                            borderColor: '#a855f7',
                            backgroundColor: 'rgba(168, 85, 247, 0.12)',
                            borderDash: [4, 4],
                            tension: 0.25,
                            spanGaps: true,
                            yAxisID: 'y1'
                        },
                        {
                            label: 'Frame Age',
                            data: series('latest_frame_age_seconds'),
                            borderColor: '#22d3ee',
                            backgroundColor: 'rgba(34, 211, 238, 0.12)',
                            borderDash: [2, 3],
                            tension: 0.25,
                            spanGaps: true,
                            yAxisID: 'y1'
                        }
                    ]
                };

                if (healthChart) {
                    healthChart.data = chartData;
                    healthChart.update('none');
                    return;
                }

                healthChart = new Chart(canvas, {
                    type: 'line',
                    data: chartData,
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { mode: 'index', intersect: false },
                        scales: {
                            x: {
                                grid: { color: 'rgba(255, 255, 255, 0.04)' },
                                ticks: {
                                    color: '#94a3b8',
                                    maxTicksLimit: 6,
                                    font: { family: 'Outfit' }
                                }
                            },
                            y: {
                                beginAtZero: true,
                                title: { display: true, text: 'ms', color: '#94a3b8' },
                                grid: { color: 'rgba(255, 255, 255, 0.05)' },
                                ticks: { color: '#94a3b8', font: { family: 'Outfit' } }
                            },
                            y1: {
                                beginAtZero: true,
                                position: 'right',
                                title: { display: true, text: 'seconds / motion', color: '#94a3b8' },
                                grid: { drawOnChartArea: false },
                                ticks: { color: '#94a3b8', font: { family: 'Outfit' } }
                            }
                        },
                        plugins: {
                            legend: {
                                labels: {
                                    color: '#f8fafc',
                                    usePointStyle: true,
                                    boxWidth: 8,
                                    font: { family: 'Outfit' }
                                }
                            },
                            tooltip: {
                                titleFont: { family: 'Outfit' },
                                bodyFont: { family: 'Outfit' }
                            }
                        }
                    }
                });
            } catch (e) {
                console.error("Error rendering health chart:", e);
            }
        }

        async function updateDashboardData() {
            if (viewMode !== 'dashboard') return;
            try {
                const statsRes = await fetch('/api/next_image?mode=queue');
                const statsData = await statsRes.json();
                
                if (statsData.stats) {
                    rtspEnabled = Boolean(statsData.stats.enable_rtsp);
                    
                    const img = document.getElementById('dash-feed-img');
                    const subtext = document.getElementById('dash-feed-subtext');
                    if (img) {
                        if (rtspEnabled) {
                            if (!img.src.includes('/api/live_stream')) {
                                img.src = '/api/live_stream';
                            }
                            if (subtext) subtext.innerText = "Continuous RTSP video streaming active.";
                        } else {
                            if (img.src.includes('/api/live_stream')) {
                                img.src = `/api/latest_image?t=${Date.now()}`;
                            }
                            if (subtext) subtext.innerText = "Updates automatically every 5s from the latest analyzed still frame.";
                        }
                    }

                    document.getElementById('stat-raw').innerText = statsData.stats.raw_count;
                    document.getElementById('stat-squirrel').innerText = statsData.stats.squirrel_count;
                    document.getElementById('stat-not-squirrel').innerText = statsData.stats.not_squirrel_count;
                    
                    const qVal = document.getElementById('dash-queue-count');
                    const qSub = document.getElementById('dash-queue-sub');
                    if (qVal) qVal.innerText = statsData.stats.raw_count;
                    if (qSub) qSub.innerText = `${statsData.stats.raw_count} raw images remaining`;

                    const hour = statsData.stats.current_hour;
                    const mtime = updateLiveSnapshotTimestamp(statsData.stats);
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

                // Calculate today's blasts locally
                const d = new Date();
                const startOfDay = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0).getTime();
                const endOfDay = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59, 999).getTime();
                const todayBlasts = (blastsData.blasts || []).filter(b => {
                    if (b.epoch) {
                        const t = b.epoch * 1000;
                        return t >= startOfDay && t <= endOfDay;
                    }
                    const dateStr = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
                    return b.timestamp && b.timestamp.startsWith(dateStr);
                });
                const todayCount = todayBlasts.length;

                if (blastedVal) blastedVal.innerText = `${todayCount} / ${blastsData.total_blasts}`;
                if (blastedSub) blastedSub.innerText = `Today / All-Time (Auto: ${blastsData.auto_blasts} | Manual: ${blastsData.manual_blasts})`;

                const accuracyVal = document.getElementById('dash-accuracy-rate');
                const accuracySub = document.getElementById('dash-accuracy-sub');
                if (accuracyVal) {
                    accuracyVal.innerText = blastsData.accuracy_rate !== null ? `${blastsData.accuracy_rate}%` : '-%';
                }
                if (accuracySub) {
                    accuracySub.innerText = `Accurate: ${blastsData.classified_accurate || 0} | False Pos: ${blastsData.classified_false_positive || 0}`;
                }

                renderBlastsChart(blastsData.blasts, blastsData.missed_squirrels || []);
                await updateHealthPanel();
                refreshDashboardSnapshot();
            } catch (e) {
                console.error("Error updating dashboard data:", e);
            }
        }

        function renderBlastsChart(blasts, missedSquirrels) {
            const ctx = document.getElementById('blasts-chart');
            if (!ctx) return;

            const days = [];
            const accurateCounts = [];
            const falseAlarms = [];
            const missedCounts = [];
            const manualCounts = [];

            for (let i = 6; i >= 0; i--) {
                const d = new Date();
                d.setDate(d.getDate() - i);
                
                const label = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
                days.push(label);

                // Define local day boundaries
                const startOfDay = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, 0).getTime();
                const endOfDay = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 23, 59, 59, 999).getTime();

                // Filter blasts that fall into this local calendar day
                const dayBlasts = blasts.filter(b => {
                    if (b.epoch) {
                        const t = b.epoch * 1000;
                        return t >= startOfDay && t <= endOfDay;
                    }
                    const dateStr = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
                    return b.timestamp && b.timestamp.startsWith(dateStr);
                });

                const dayMissed = missedSquirrels.filter(m => {
                    if (m.epoch) {
                        const t = m.epoch * 1000;
                        return t >= startOfDay && t <= endOfDay;
                    }
                    const dateStr = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
                    return m.timestamp && m.timestamp.startsWith(dateStr);
                });

                // Accurate detections (auto blasts that are not false positives)
                const accurate = dayBlasts.filter(b => b.type === 'auto' && b.classification !== 'false_positive').length;
                accurateCounts.push(accurate);

                // False positives
                const fp = dayBlasts.filter(b => b.type === 'auto' && b.classification === 'false_positive').length;
                falseAlarms.push(fp);

                // Missed squirrels
                missedCounts.push(dayMissed.length);

                // Manual sprays
                const manual = dayBlasts.filter(b => b.type === 'manual').length;
                manualCounts.push(manual);
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
                            label: 'Accurate Auto-Detections',
                            data: accurateCounts,
                            backgroundColor: 'rgba(16, 185, 129, 0.75)',
                            borderColor: 'var(--color-squirrel)',
                            borderWidth: 1,
                            borderRadius: 6
                        },
                        {
                            label: 'False Alarms (FP)',
                            data: falseAlarms,
                            backgroundColor: 'rgba(245, 158, 11, 0.75)',
                            borderColor: 'var(--color-delete)',
                            borderWidth: 1,
                            borderRadius: 6
                        },
                        {
                            label: 'Missed Squirrels (FN)',
                            data: missedCounts,
                            backgroundColor: 'rgba(239, 68, 68, 0.75)',
                            borderColor: 'var(--color-not-squirrel)',
                            borderWidth: 1,
                            borderRadius: 6
                        },
                        {
                            label: 'Manual Sprays',
                            data: manualCounts,
                            backgroundColor: 'rgba(59, 130, 246, 0.75)',
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
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Analysis Interval (seconds)</label>
                        <input type="number" id="settings-analysis-interval" min="5" max="300" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">How often the Pi Camera captures and sends stills for inference. Default: 5s.</span>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Review Save Interval (seconds)</label>
                        <input type="number" id="settings-save-interval" min="5" max="3600" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">How often normal analysis frames are saved for review/classification. Default: 30s.</span>
                    </div>

                    <div style="border-top: 1px solid var(--border-color); padding-top: 1.25rem; margin-top: 0.5rem; display: flex; flex-direction: column; gap: 1rem;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary); margin-bottom: -0.25rem;">Daylight Schedule</h3>
                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Nighttime Mode</label>
                            <select id="settings-daylight-mode" style="background: #0f172a; border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem; cursor: pointer;">
                                <option value="sun">Sunrise/Sunset</option>
                                <option value="fixed">Fixed Hours</option>
                            </select>
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Sunrise/sunset defaults to Reston, VA and keeps the Pi quiet overnight.</span>
                        </div>
                        <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.75rem;">
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Latitude</label>
                                <input type="number" id="settings-daylight-latitude" min="-90" max="90" step="0.0001" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Longitude</label>
                                <input type="number" id="settings-daylight-longitude" min="-180" max="180" step="0.0001" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Sunrise Offset (minutes)</label>
                                <input type="number" id="settings-sunrise-offset" min="-180" max="180" step="5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Sunset Offset (minutes)</label>
                                <input type="number" id="settings-sunset-offset" min="-180" max="180" step="5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Fixed Start Hour</label>
                                <input type="number" id="settings-daylight-start-hour" min="0" max="23" step="1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Fixed End Hour</label>
                                <input type="number" id="settings-daylight-end-hour" min="0" max="23" step="1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                        </div>
                    </div>

                    <div style="border-top: 1px solid var(--border-color); padding-top: 1.25rem; margin-top: 0.5rem; display: flex; flex-direction: column; gap: 1rem;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary); margin-bottom: -0.25rem;">Capture Quality & Motion</h3>
                        <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.75rem;">
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Live Width</label>
                                <input type="number" id="settings-analysis-width" min="320" max="1920" step="16" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Live Height</label>
                                <input type="number" id="settings-analysis-height" min="240" max="1440" step="16" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Live JPEG Quality</label>
                                <input type="number" id="settings-analysis-quality" min="30" max="95" step="5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Review JPEG Quality</label>
                                <input type="number" id="settings-review-quality" min="50" max="100" step="5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                        </div>
                        <div style="display: flex; align-items: center; gap: 0.75rem;">
                            <input type="checkbox" id="settings-motion-enabled" style="width: 1.2rem; height: 1.2rem; cursor: pointer;">
                            <label for="settings-motion-enabled" style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary); cursor: pointer;">Enable Motion Prefilter</label>
                        </div>
                        <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.75rem;">
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Motion Threshold</label>
                                <input type="number" id="settings-motion-threshold" min="1" max="40" step="0.5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Force Analysis Every (seconds)</label>
                                <input type="number" id="settings-motion-force" min="5" max="600" step="5" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                        </div>
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

                    <div style="border-top: 1px solid var(--border-color); padding-top: 1.25rem; margin-top: 0.5rem; display: flex; flex-direction: column; gap: 1rem;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary); margin-bottom: -0.25rem;">Camera Calibration</h3>
                        <div style="display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem;">
                            <div>
                                <div style="font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 0.45rem;">Latest captured output</div>
                                <div style="position: relative; width: 100%; aspect-ratio: 4 / 3; background: #020617; border: 1px solid var(--border-color); border-radius: 12px; overflow: hidden;">
                                    <img id="settings-calibration-img" src="/api/latest_image?t=${Date.now()}" style="width: 100%; height: 100%; object-fit: cover;">
                                </div>
                            </div>
                            <div>
                                <div style="font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 0.45rem;">Full camera frame ROI map</div>
                                <div id="settings-roi-map" style="position: relative; width: 100%; aspect-ratio: 4 / 3; background: linear-gradient(90deg, rgba(148, 163, 184, 0.08) 1px, transparent 1px), linear-gradient(rgba(148, 163, 184, 0.08) 1px, transparent 1px), #020617; background-size: 25% 25%; border: 1px solid var(--border-color); border-radius: 12px; overflow: hidden;">
                                    <div id="settings-calibration-roi-box" style="position: absolute; border: 2px solid #34d399; background: rgba(52, 211, 153, 0.12); box-shadow: 0 0 0 9999px rgba(2, 6, 23, 0.28); display: none;"></div>
                                </div>
                            </div>
                        </div>
                        <div id="settings-calibration-details" style="font-size: 0.8rem; color: var(--text-secondary); line-height: 1.5;"></div>
                    </div>

                    <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                        <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">SQUIRREL Detection Confidence Threshold</label>
                        <input type="number" id="settings-confidence" min="0.50" max="0.99" step="0.05" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">Model probability threshold (0.50 - 0.99) for a qualifying squirrel detection. Default: 0.70.</span>
                    </div>

                    <div style="border-top: 1px solid var(--border-color); padding-top: 1.25rem; margin-top: 0.5rem; display: flex; flex-direction: column; gap: 1rem;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary); margin-bottom: -0.25rem;">Spray Decision Gate</h3>
                        <div style="display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.75rem;">
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Required Hits</label>
                                <input type="number" id="settings-decision-hits" min="1" max="5" step="1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Window Seconds</label>
                                <input type="number" id="settings-decision-window" min="3" max="60" step="1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                            <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                                <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Average Confidence</label>
                                <input type="number" id="settings-decision-average" min="0.50" max="0.99" step="0.05" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            </div>
                        </div>
                        <span style="font-size: 0.75rem; color: var(--text-secondary);">Detection can be true without spraying; spraying waits for repeated high-confidence hits in this window.</span>
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
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary); margin-bottom: -0.25rem;">RTSP Stream Settings 🎥</h3>

                        <div style="display: flex; align-items: center; gap: 0.75rem; margin-top: 0.25rem;">
                            <input type="checkbox" id="settings-enable-rtsp" style="width: 1.2rem; height: 1.2rem; cursor: pointer;">
                            <label for="settings-enable-rtsp" style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary); cursor: pointer;">Enable RTSP Streaming Backend</label>
                        </div>
                        
                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">RTSP Stream URL</label>
                            <input type="text" id="settings-rtsp-url" placeholder="rtsp://pi3:8554/live" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">The RTSP source URL of the Pi camera stream.</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Training Stills Motion Save Interval (minutes)</label>
                            <input type="number" id="settings-rtsp-motion-interval" min="1" max="60" step="1" style="background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Minimum time to wait between auto-saving negative candidate images to data/raw/ when motion is detected. Default: 5 min.</span>
                        </div>
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

                    <div style="border-top: 1px solid var(--border-color); padding-top: 1.25rem; margin-top: 0.5rem; display: flex; flex-direction: column; gap: 1.25rem;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary); margin-bottom: -0.25rem;">Inference Model Settings 🧠</h3>
                        
                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Active Detection Model</label>
                            <select id="settings-active-model" style="background: #0f172a; border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem; cursor: pointer;">
                                <!-- populated dynamically -->
                            </select>
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Select the model used for real-time squirrel detection. YOLO (.pt) or ResNet-18 (.pth).</span>
                        </div>

                        <div style="display: flex; flex-direction: column; gap: 0.4rem;">
                            <label style="font-weight: 600; font-size: 0.9rem; color: var(--text-primary);">Save Current Trained Model Checkpoint</label>
                            <div style="display: flex; gap: 0.5rem;">
                                <input type="text" id="settings-checkpoint-name" placeholder="e.g. resnet18_morning" style="flex-grow: 1; background: rgba(15, 23, 42, 0.6); border: 1px solid var(--border-color); border-radius: 8px; padding: 0.75rem; color: white; font-family: Outfit; font-size: 0.95rem;">
                                <button type="button" class="btn" style="background-color: var(--color-add); color: white; border-radius: 8px; padding: 0.75rem 1.25rem;" onclick="saveCheckpoint()">Save Checkpoint 💾</button>
                            </div>
                            <span style="font-size: 0.75rem; color: var(--text-secondary);">Copies the active root model.pth checkpoint to a named file (saved in data/models/).</span>
                        </div>
                    </div>
                    
                    <div style="border-top: 1px solid var(--border-color); padding-top: 1.25rem; margin-top: 0.5rem; display: flex; flex-direction: column; gap: 1rem;">
                        <h3 style="font-size: 1.05rem; font-weight: 600; color: var(--text-primary);">Model Accuracy Tracker 📊</h3>
                        <div style="overflow-x: auto; width: 100%; border: 1px solid var(--border-color); border-radius: 12px; background: rgba(15, 23, 42, 0.4);">
                            <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 0.9rem;">
                                <thead>
                                    <tr style="border-bottom: 1px solid var(--border-color); background: rgba(255,255,255,0.02);">
                                        <th style="padding: 0.75rem 1rem; font-weight: 600; color: var(--text-secondary);">Model Name</th>
                                        <th style="padding: 0.75rem 1rem; font-weight: 600; color: var(--text-secondary); text-align: center;">Total Sprays</th>
                                        <th style="padding: 0.75rem 1rem; font-weight: 600; color: var(--text-secondary); text-align: center;">Accurate</th>
                                        <th style="padding: 0.75rem 1rem; font-weight: 600; color: var(--text-secondary); text-align: center;">False Positive</th>
                                        <th style="padding: 0.75rem 1rem; font-weight: 600; color: var(--text-secondary); text-align: center;">Accuracy Rate</th>
                                    </tr>
                                </thead>
                                <tbody id="accuracies-table-body">
                                    <tr>
                                        <td colspan="5" style="padding: 1.5rem; text-align: center; color: var(--text-secondary);">No model stats recorded yet.</td>
                                    </tr>
                                </tbody>
                            </table>
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

        function renderAccuraciesTable(accuracies) {
            const tbody = document.getElementById('accuracies-table-body');
            if (!tbody) return;
            
            if (!accuracies || Object.keys(accuracies).length === 0) {
                tbody.innerHTML = `
                    <tr>
                        <td colspan="5" style="padding: 1.5rem; text-align: center; color: var(--text-secondary);">No model stats recorded yet.</td>
                    </tr>
                `;
                return;
            }
            
            tbody.innerHTML = '';
            for (const [modelName, stats] of Object.entries(accuracies)) {
                const tr = document.createElement('tr');
                tr.style.borderBottom = '1px solid var(--border-color)';
                
                const rateText = stats.accuracy_rate !== null ? stats.accuracy_rate + '%' : 'N/A';
                let badgeColor = 'var(--text-secondary)';
                if (stats.accuracy_rate !== null) {
                    if (stats.accuracy_rate >= 85) badgeColor = '#34d399';
                    else if (stats.accuracy_rate >= 60) badgeColor = '#f59e0b';
                    else badgeColor = '#f87171';
                }
                
                tr.innerHTML = `
                    <td style="padding: 0.75rem 1rem; font-family: monospace; font-size: 0.85rem; color: var(--text-primary); word-break: break-all;">${modelName}</td>
                    <td style="padding: 0.75rem 1rem; text-align: center; color: var(--text-primary);">${stats.total}</td>
                    <td style="padding: 0.75rem 1rem; text-align: center; color: #34d399;">${stats.accurate}</td>
                    <td style="padding: 0.75rem 1rem; text-align: center; color: #f87171;">${stats.false_positive}</td>
                    <td style="padding: 0.75rem 1rem; text-align: center; font-weight: 600; color: ${badgeColor};">${rateText}</td>
                `;
                tbody.appendChild(tr);
            }
        }

        async function saveCheckpoint() {
            const nameInput = document.getElementById('settings-checkpoint-name');
            if (!nameInput) return;
            const name = nameInput.value.trim();
            if (!name) {
                alert("Please enter a name for the checkpoint.");
                return;
            }
            
            try {
                const res = await fetch('/api/settings/save_model', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    alert("Checkpoint saved successfully!");
                    nameInput.value = '';
                    if (data.available_models) {
                        const activeModelSelect = document.getElementById('settings-active-model');
                        if (activeModelSelect) {
                            const currentSelected = activeModelSelect.value;
                            activeModelSelect.innerHTML = '';
                            data.available_models.forEach(modelName => {
                                const opt = document.createElement('option');
                                opt.value = modelName;
                                opt.textContent = modelName;
                                if (modelName === currentSelected || modelName === data.filename) {
                                    opt.selected = true;
                                }
                                activeModelSelect.appendChild(opt);
                            });
                        }
                    }
                } else {
                    alert("Error saving checkpoint: " + data.message);
                }
            } catch (e) {
                console.error("Error saving checkpoint:", e);
                alert("Failed to save checkpoint.");
            }
        }

        async function fetchSettings() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                if (data.status === 'success') {
                    document.getElementById('settings-analysis-interval').value = data.settings.analysis_interval || data.settings.capture_interval || 5;
                    document.getElementById('settings-save-interval').value = data.settings.save_interval || 30;
                    document.getElementById('settings-daylight-mode').value = data.settings.daylight_mode || 'sun';
                    document.getElementById('settings-daylight-latitude').value = data.settings.daylight_latitude ?? 38.9586;
                    document.getElementById('settings-daylight-longitude').value = data.settings.daylight_longitude ?? -77.3570;
                    document.getElementById('settings-sunrise-offset').value = data.settings.sunrise_offset_minutes ?? 0;
                    document.getElementById('settings-sunset-offset').value = data.settings.sunset_offset_minutes ?? 0;
                    document.getElementById('settings-daylight-start-hour').value = data.settings.daylight_start_hour ?? 6;
                    document.getElementById('settings-daylight-end-hour').value = data.settings.daylight_end_hour ?? 20;
                    document.getElementById('settings-analysis-width').value = data.settings.analysis_width || 960;
                    document.getElementById('settings-analysis-height').value = data.settings.analysis_height || 720;
                    document.getElementById('settings-analysis-quality').value = data.settings.analysis_jpeg_quality || 65;
                    document.getElementById('settings-review-quality').value = data.settings.review_jpeg_quality || 90;
                    document.getElementById('settings-motion-enabled').checked = data.settings.motion_prefilter_enabled !== false;
                    document.getElementById('settings-motion-threshold').value = data.settings.motion_threshold || 6.0;
                    document.getElementById('settings-motion-force').value = data.settings.motion_force_interval || 30;
                    document.getElementById('settings-gemini-key').value = data.settings.gemini_api_key;
                    document.getElementById('settings-rotation').value = data.settings.camera_rotation;
                    document.getElementById('settings-roi').value = data.settings.camera_roi;
                    document.getElementById('settings-video-roi').value = data.settings.video_roi || '';
                    document.getElementById('settings-confidence').value = data.settings.confidence_threshold;
                    document.getElementById('settings-decision-hits').value = data.settings.spray_decision_required_hits ?? 2;
                    document.getElementById('settings-decision-window').value = data.settings.spray_decision_window_seconds ?? 12;
                    document.getElementById('settings-decision-average').value = data.settings.spray_decision_average_confidence ?? 0.75;
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
                    
                    document.getElementById('settings-enable-rtsp').checked = data.settings.enable_rtsp !== false;
                    document.getElementById('settings-rtsp-url').value = data.settings.rtsp_stream_url || 'rtsp://pi3:8554/live';
                    document.getElementById('settings-rtsp-motion-interval').value = data.settings.rtsp_motion_interval_minutes || 5;

                    // Populate active model dropdown
                    const activeModelSelect = document.getElementById('settings-active-model');
                    if (activeModelSelect) {
                        activeModelSelect.innerHTML = '';
                        if (data.available_models && data.available_models.length) {
                            data.available_models.forEach(modelName => {
                                const opt = document.createElement('option');
                                opt.value = modelName;
                                opt.textContent = modelName;
                                if (modelName === data.settings.active_model) {
                                    opt.selected = true;
                                }
                                activeModelSelect.appendChild(opt);
                            });
                        }
                    }

                    // Render model accuracies
                    if (data.model_accuracies) {
                        renderAccuraciesTable(data.model_accuracies);
                    }
                    updateSettingsCalibration(data.settings);
                    const roiInput = document.getElementById('settings-roi');
                    if (roiInput && !roiInput.dataset.calibrationBound) {
                        roiInput.addEventListener('input', () => updateSettingsCalibration(data.settings));
                        roiInput.dataset.calibrationBound = 'true';
                    }
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

            const analysis_interval = parseInt(document.getElementById('settings-analysis-interval').value);
            const save_interval = parseInt(document.getElementById('settings-save-interval').value);
            const capture_interval = analysis_interval;
            const daylight_mode = document.getElementById('settings-daylight-mode').value;
            const daylight_latitude = parseFloat(document.getElementById('settings-daylight-latitude').value);
            const daylight_longitude = parseFloat(document.getElementById('settings-daylight-longitude').value);
            const sunrise_offset_minutes = parseInt(document.getElementById('settings-sunrise-offset').value);
            const sunset_offset_minutes = parseInt(document.getElementById('settings-sunset-offset').value);
            const daylight_start_hour = parseInt(document.getElementById('settings-daylight-start-hour').value);
            const daylight_end_hour = parseInt(document.getElementById('settings-daylight-end-hour').value);
            const analysis_width = parseInt(document.getElementById('settings-analysis-width').value);
            const analysis_height = parseInt(document.getElementById('settings-analysis-height').value);
            const analysis_jpeg_quality = parseInt(document.getElementById('settings-analysis-quality').value);
            const review_jpeg_quality = parseInt(document.getElementById('settings-review-quality').value);
            const motion_prefilter_enabled = document.getElementById('settings-motion-enabled').checked;
            const motion_threshold = parseFloat(document.getElementById('settings-motion-threshold').value);
            const motion_force_interval = parseInt(document.getElementById('settings-motion-force').value);
            const gemini_api_key = document.getElementById('settings-gemini-key').value;
            const camera_rotation = parseInt(document.getElementById('settings-rotation').value);
            const camera_roi = document.getElementById('settings-roi').value;
            const video_roi = document.getElementById('settings-video-roi').value;
            const confidence_threshold = parseFloat(document.getElementById('settings-confidence').value);
            const spray_decision_required_hits = parseInt(document.getElementById('settings-decision-hits').value);
            const spray_decision_window_seconds = parseInt(document.getElementById('settings-decision-window').value);
            const spray_decision_average_confidence = parseFloat(document.getElementById('settings-decision-average').value);
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
            const active_model = document.getElementById('settings-active-model') ? document.getElementById('settings-active-model').value : '';
            
            const enable_rtsp = document.getElementById('settings-enable-rtsp').checked;
            const rtsp_stream_url = document.getElementById('settings-rtsp-url').value;
            const rtsp_motion_interval_minutes = parseInt(document.getElementById('settings-rtsp-motion-interval').value);

            try {
                const res = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        capture_interval,
                        analysis_interval,
                        save_interval,
                        daylight_mode,
                        daylight_latitude,
                        daylight_longitude,
                        sunrise_offset_minutes,
                        sunset_offset_minutes,
                        daylight_start_hour,
                        daylight_end_hour,
                        analysis_width,
                        analysis_height,
                        analysis_jpeg_quality,
                        review_jpeg_quality,
                        motion_prefilter_enabled,
                        motion_threshold,
                        motion_force_interval,
                        gemini_api_key,
                        camera_rotation,
                        camera_roi,
                        video_roi,
                        confidence_threshold,
                        spray_decision_required_hits,
                        spray_decision_window_seconds,
                        spray_decision_average_confidence,
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
                        email_to,
                        active_model,
                        enable_rtsp,
                        rtsp_stream_url,
                        rtsp_motion_interval_minutes
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    badge.style.display = 'inline-block';
                    setTimeout(() => {
                        badge.style.display = 'none';
                    }, 4000);
                    
                    if (data.available_models) {
                        const activeModelSelect = document.getElementById('settings-active-model');
                        if (activeModelSelect) {
                            activeModelSelect.innerHTML = '';
                            data.available_models.forEach(modelName => {
                                const opt = document.createElement('option');
                                opt.value = modelName;
                                opt.textContent = modelName;
                                if (modelName === data.settings.active_model) {
                                    opt.selected = true;
                                }
                                activeModelSelect.appendChild(opt);
                            });
                        }
                    }
                    if (data.model_accuracies) {
                        renderAccuraciesTable(data.model_accuracies);
                    }
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
                    <button id="false-alarm-train-btn" class="btn" style="margin-left: 0.5rem; background-color: transparent; border: 1px solid var(--color-delete); color: var(--color-delete);" onclick="prepareFalseAlarmTraining()">
                        Build False Alarm Negatives
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

        async function prepareFalseAlarmTraining() {
            const btn = document.getElementById('false-alarm-train-btn');
            const originalText = btn ? btn.innerText : '';
            if (btn) {
                btn.disabled = true;
                btn.innerText = 'Building...';
            }
            try {
                const res = await fetch('/api/train/false_alarms', { method: 'POST' });
                const data = await res.json();
                if (data.status === 'success') {
                    const result = data.result || {};
                    alert(`Created ${result.created || 0} hard-negative frames from ${result.processed || 0} false-alarm videos.`);
                    checkTrainStatus();
                } else {
                    alert(data.message);
                }
            } catch (e) {
                console.error("Error preparing false alarm training data:", e);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerText = originalText || 'Build False Alarm Negatives';
                }
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
                    currentImageConfidence = data.confidence;
                    currentIndex = data.index;
                    totalCount = data.total;
                    
                    workspace.innerHTML = `
                        <div class="image-container" id="img-container">
                            <img src="/image/${data.image}" alt="Feeder image">
                        </div>
                        <div class="image-filename" style="margin-top: 1rem; font-family: monospace; font-size: 0.9rem; color: var(--text-secondary); text-align: center;">
                            ${data.image}
                        </div>
                        ${confidenceBadge(currentImageConfidence)}
                        
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
                    currentImageConfidence = null;
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
                galleryImageMeta = data.image_meta || {};
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
                                <img src="/api/thumbnail/${img}" alt="Still preview">
                                <div class="gallery-card-info">
                                    <span class="gallery-filename">${img}</span>
                                    ${confidenceBadge(galleryImageMeta[img]?.confidence, true)}
                                </div>
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
                    
                    let topPaginationHtml = '';
                    if (totalPages > 1) {
                        topPaginationHtml = `
                            <div class="pagination-container" style="margin-top: 0; margin-bottom: 1.5rem;">
                                ${pageLinksHtml}
                            </div>
                        `;
                    }
                    
                    workspace.innerHTML = `
                        <div style="width: 100%; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1rem;">
                            <h2 style="font-weight: 600; font-size: 1.25rem;">${titleText}</h2>
                            <span style="font-size: 0.9rem; color: var(--text-secondary);">
                                Showing ${(currentPage - 1) * 12 + 1} - ${Math.min(currentPage * 12, galleryTotalCount)} of ${galleryTotalCount}
                            </span>
                        </div>
                        ${topPaginationHtml}
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
                allLoadedVideos = videos || [];
                videoClassifications = data.classifications || {};
                videoFavorites = data.favorites || {};
                const falseAlarmVideoCount = data.false_alarm_video_count || 0;
                
                // Extract unique dates from the videos list
                const datesSet = new Set();
                if (videos) {
                    videos.forEach(vid => {
                        const match = vid.match(/^vid_(\\d{4})(\\d{2})(\\d{2})_(\\d{2})(\\d{2})(\\d{2})\\.mp4$/);
                        if (match) {
                            datesSet.add(`${match[1]}-${match[2]}-${match[3]}`);
                        }
                    });
                }
                const sortedDates = Array.from(datesSet).sort((a, b) => b.localeCompare(a));
                
                if (videos && videos.length > 0) {
                    let filteredVideos = videos;
                    if (showFavoritesOnly) {
                        filteredVideos = videos.filter(vid => videoFavorites[vid] === true);
                    }

                    const videosPerPage = 12;
                    const videoTotalPages = Math.ceil(filteredVideos.length / videosPerPage) || 1;
                    if (videoCurrentPage > videoTotalPages) {
                        videoCurrentPage = videoTotalPages;
                    }
                    if (videoCurrentPage < 1) {
                        videoCurrentPage = 1;
                    }
                    const startIndex = (videoCurrentPage - 1) * videosPerPage;
                    const endIndex = Math.min(startIndex + videosPerPage, filteredVideos.length);
                    const videosToRender = filteredVideos.slice(startIndex, endIndex);

                    let rangeStr = '';
                    if (filteredVideos.length > 0) {
                        rangeStr = `Showing ${startIndex + 1} - ${endIndex} of ${filteredVideos.length}`;
                    } else {
                        rangeStr = `Showing 0 - 0 of 0`;
                    }

                    // Get today's local date YYYY-MM-DD
                    const today = new Date();
                    const year = today.getFullYear();
                    const month = String(today.getMonth() + 1).padStart(2, '0');
                    const day = String(today.getDate()).padStart(2, '0');
                    const todayStr = `${year}-${month}-${day}`;

                    // Build standard header html
                    let headerHtml = `
                        <div style="width: 100%; display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 1rem; margin-bottom: 1rem; gap: 1rem;">
                            <div style="display: flex; align-items: center; flex-wrap: wrap; gap: 0.75rem;">
                                <h2 style="font-weight: 600; font-size: 1.25rem; margin: 0;">Spray Videos</h2>
                                <button id="favorites-filter-btn" class="btn" style="padding: 0.35rem 0.75rem; font-size: 0.8rem; display: flex; align-items: center; gap: 0.35rem; background-color: ${showFavoritesOnly ? '#f59e0b' : 'transparent'}; border: 1px solid ${showFavoritesOnly ? '#f59e0b' : 'var(--border-color)'}; color: ${showFavoritesOnly ? '#020617' : 'var(--text-secondary)'}; font-weight: 600; border-radius: 8px; cursor: pointer; transition: all 0.15s ease;" onclick="toggleFavoritesFilter()">
                                    ⭐ ${showFavoritesOnly ? 'Favorites Only' : 'All Videos'}
                                </button>
                                ${falseAlarmVideoCount > 0 ? `
                                <button class="btn" style="padding: 0.35rem 0.75rem; font-size: 0.8rem; display: flex; align-items: center; gap: 0.35rem; background-color: transparent; border: 1px solid var(--color-delete); color: var(--color-delete); font-weight: 600; border-radius: 8px; cursor: pointer; transition: all 0.15s ease;" onclick="deleteAllFalseAlarmVideos()">
                                    Delete False Alarms (${falseAlarmVideoCount})
                                </button>
                                ` : ''}
                                ${sortedDates.length > 0 ? `
                                <div style="display: flex; align-items: center; gap: 0.5rem; margin-left: 0.5rem;">
                                    <select id="compilation-date-select" style="padding: 0.35rem 0.75rem; font-size: 0.8rem; background-color: rgba(255,255,255,0.05); border: 1px solid var(--border-color); color: var(--text-primary); border-radius: 8px; cursor: pointer; outline: none; font-weight: 600;">
                                        ${sortedDates.map(d => {
                                            const isSelected = (d === todayStr) ? 'selected' : '';
                                            return `<option value="${d}" ${isSelected}>${formatDateString(d)}</option>`;
                                        }).join('')}
                                    </select>
                                    <button class="btn" style="padding: 0.35rem 0.75rem; font-size: 0.8rem; background-color: var(--color-squirrel, #10b981); border: 1px solid var(--color-squirrel, #10b981); color: #020617; font-weight: 600; border-radius: 8px; cursor: pointer; display: flex; align-items: center; gap: 0.25rem;" onclick="startCompilationPlaylist()">
                                        Play Compilation 🍿
                                    </button>
                                </div>
                                ` : ''}
                            </div>
                            <span style="font-size: 0.9rem; color: var(--text-secondary);">${rangeStr}</span>
                        </div>
                    `;

                    if (filteredVideos.length > 0) {
                        let cardsHtml = '';
                        videosToRender.forEach((vid) => {
                            const currentClassification = videoClassifications[vid] || null;
                            const isAccurate = currentClassification === 'accurate';
                            const isFalsePositive = currentClassification === 'false_positive';
                            const isFav = videoFavorites[vid] || false;
                            
                            cardsHtml += `
                                <div class="gallery-card" onclick="openVideoModal('${vid}')">
                                    ${isFav ? `
                                    <button class="action-icon-btn" 
                                            style="position: absolute; top: 8px; left: 8px; background: rgba(245, 158, 11, 0.25); border: 1px solid #f59e0b; color: #f59e0b; border-radius: 8px; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 6; font-size: 1rem; transition: all 0.15s ease;"
                                            onclick="event.stopPropagation(); toggleFavoriteVideo('${vid}', false)" 
                                            title="Unfavorite Video">
                                        ⭐
                                    </button>
                                    ` : ''}
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

                        // Build page links for videos
                        let pageLinksHtml = `
                            <button class="page-link" onclick="setVideoPage(1)" ${videoCurrentPage === 1 ? 'disabled' : ''}>&lt;&lt;</button>
                            <button class="page-link" onclick="setVideoPage(${videoCurrentPage - 1})" ${videoCurrentPage === 1 ? 'disabled' : ''}>&lt;</button>
                        `;
                        
                        let startPage = Math.max(1, videoCurrentPage - 2);
                        let endPage = Math.min(videoTotalPages, startPage + 4);
                        if (endPage - startPage < 4) {
                            startPage = Math.max(1, endPage - 4);
                        }
                        
                        for (let p = startPage; p <= endPage; p++) {
                            pageLinksHtml += `
                                <button class="page-link ${p === videoCurrentPage ? 'active' : ''}" onclick="setVideoPage(${p})">${p}</button>
                            `;
                        }
                        
                        pageLinksHtml += `
                            <button class="page-link" onclick="setVideoPage(${videoCurrentPage + 1})" ${videoCurrentPage === videoTotalPages ? 'disabled' : ''}>&gt;</button>
                            <button class="page-link" onclick="setVideoPage(${videoTotalPages})" ${videoCurrentPage === videoTotalPages ? 'disabled' : ''}>&gt;&gt;</button>
                        `;
                        
                        let topPaginationHtml = '';
                        if (videoTotalPages > 1) {
                            topPaginationHtml = `
                                <div class="pagination-container" style="margin-top: 0; margin-bottom: 1.5rem;">
                                    ${pageLinksHtml}
                                </div>
                            `;
                        }

                        workspace.innerHTML = `
                            ${headerHtml}
                            ${topPaginationHtml}
                            <div class="grid-gallery">
                                ${cardsHtml}
                            </div>
                            <div class="pagination-container">
                                ${pageLinksHtml}
                            </div>
                        `;
                    } else {
                        workspace.innerHTML = `
                            ${headerHtml}
                            <div class="no-images">
                                <div class="no-images-icon" style="color: #f59e0b;">⭐</div>
                                <h2>No favorite videos found</h2>
                                <p>Click the star icon on any video card to add it to your favorites!</p>
                            </div>
                        `;
                    }
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

        async function deleteAllFalseAlarmVideos() {
            if (!confirm('Delete all video files marked as false alarms? The false-alarm history and accuracy graph will be kept.')) return;
            try {
                const res = await fetch('/api/delete_false_alarm_videos', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await res.json();
                if (data.status === 'success') {
                    loadNext();
                } else {
                    alert("Delete failed: " + data.message);
                }
            } catch (e) {
                console.error("Error deleting false alarm videos:", e);
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
            const meta = galleryImageMeta[img] || {};
            document.getElementById('modal-img-element').src = `/image/${img}`;
            document.getElementById('modal-img-filename').innerHTML = `
                <div>${img}</div>
                ${confidenceBadge(meta.confidence)}
            `;
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
            document.getElementById('modal-video-element').src = `/video/${filename}?t=${Date.now()}`;
            document.getElementById('modal-video-filename').innerText = filename;
            updateModalVideoClassifications(filename);
            document.getElementById('video-modal').classList.add('show');
        }

        function updateModalVideoClassifications(filename) {
            const container = document.getElementById('modal-video-classification-actions');
            if (!container) return;
            
            if (filename.startsWith('compilation_')) {
                container.innerHTML = `
                    <button class="btn" onclick="shareVideo('${filename}')">
                        🔗 Share Compilation
                    </button>
                    <a class="btn" href="/video/${filename}?t=${Date.now()}" download="${filename}">
                        📥 Download Compilation
                    </a>
                `;
                return;
            }
            
            const currentClassification = videoClassifications[filename] || null;
            const isAccurate = currentClassification === 'accurate';
            const isFalsePositive = currentClassification === 'false_positive';
            const isFav = videoFavorites[filename] || false;
            
            container.innerHTML = `
                <button class="btn ${isFav ? 'favorite-active' : ''}" 
                        onclick="toggleFavoriteVideoModal('${filename}', ${!isFav})">
                    ⭐ ${isFav ? 'Favorited' : 'Favorite'}
                </button>
                <button class="btn" onclick="shareVideo('${filename}')">
                    🔗 Share
                </button>
                <a class="btn" href="/video/${filename}?t=${Date.now()}" download="${filename}">
                    📥 Download
                </a>
                <div class="separator"></div>
                <button class="btn ${isAccurate ? 'accurate-active' : ''}" 
                        onclick="classifyVideoModal('${filename}', '${isAccurate ? '' : 'accurate'}')">
                    Accurate 🐿️
                </button>
                <button class="btn ${isFalsePositive ? 'false-positive-active' : ''}" 
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
            player.loop = true; // Restore loop default
            
            // Hide compilation HUD elements
            const compHeader = document.getElementById('compilation-header');
            if (compHeader) compHeader.style.display = 'none';
            
            document.getElementById('video-modal').classList.remove('show');
        }

        function openPreviewModal(src, title = "Live Snapshot Preview") {
            const modal = document.getElementById('preview-modal');
            const img = document.getElementById('preview-modal-img');
            const titleEl = document.getElementById('preview-modal-title');
            if (modal && img) {
                img.src = src;
                if (titleEl) titleEl.innerText = title;
                modal.classList.add('show');
            }
        }

        function closePreviewModal() {
            const modal = document.getElementById('preview-modal');
            if (modal) {
                modal.classList.remove('show');
            }
        }

        function getParsedDate(filename) {
            const match = filename.match(/^vid_(\\d{4})(\\d{2})(\\d{2})_(\\d{2})(\\d{2})(\\d{2})\\.mp4$/);
            if (match) {
                return `${match[1]}-${match[2]}-${match[3]}`;
            }
            return null;
        }

        function formatDateString(dateStr) {
            const parts = dateStr.split('-');
            if (parts.length === 3) {
                const year = parts[0];
                const monthIndex = parseInt(parts[1], 10) - 1;
                const day = parseInt(parts[2], 10);
                const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
                if (monthIndex >= 0 && monthIndex < 12) {
                    return `${months[monthIndex]} ${day}, ${year}`;
                }
            }
            return dateStr;
        }

        async function startCompilationPlaylist() {
            const selectEl = document.getElementById('compilation-date-select');
            if (!selectEl) return;
            const selectedDate = selectEl.value;
            if (!selectedDate) {
                alert("Please select a date from the dropdown first! 🍿");
                return;
            }
            
            const btn = document.querySelector('button[onclick="startCompilationPlaylist()"]');
            const originalText = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = "Stitching... 🍿";
            
            try {
                const res = await fetch(`/api/compilation/${selectedDate}`);
                const data = await res.json();
                
                if (data.status === 'success') {
                    playCompilationVideo(data.filename, selectedDate);
                } else {
                    alert("Failed to build compilation: " + data.message);
                }
            } catch (e) {
                console.error("Error creating compilation:", e);
                alert("Error connecting to server to build compilation.");
            } finally {
                btn.disabled = false;
                btn.innerHTML = originalText;
            }
        }

        function playCompilationVideo(filename, selectedDate) {
            const player = document.getElementById('modal-video-element');
            player.src = `/video/${filename}?t=${Date.now()}`;
            player.loop = false; // Compilations don't loop by default
            
            document.getElementById('modal-video-filename').innerText = filename;
            updateModalVideoClassifications(filename);
            
            // Update HUD text
            const compHeader = document.getElementById('compilation-header');
            if (compHeader) {
                document.getElementById('compilation-header-info').innerText = `Daily Stitched Video - ${formatDateString(selectedDate)}`;
                compHeader.style.display = 'flex';
            }
            
            document.getElementById('video-modal').classList.add('show');
        }

        async function triggerSpray() {
            const btn = document.getElementById('spray-btn');
            const text = document.getElementById('spray-text');
            const spinner = document.getElementById('spray-spinner');
            
            const modalBtn = document.getElementById('modal-spray-btn');
            const modalText = document.getElementById('modal-spray-text');
            const modalSpinner = document.getElementById('modal-spray-spinner');
            
            if (btn) {
                btn.disabled = true;
                if (spinner) spinner.style.display = 'inline-block';
                if (text) text.innerText = 'Spraying...';
            }
            if (modalBtn) {
                modalBtn.disabled = true;
                if (modalSpinner) modalSpinner.style.display = 'inline-block';
                if (modalText) modalText.innerText = 'Spraying...';
            }
            
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
                if (spinner) spinner.style.display = 'none';
                if (text) text.innerText = 'Spray 💦';
                if (btn) btn.disabled = false;
                
                if (modalSpinner) modalSpinner.style.display = 'none';
                if (modalText) modalText.innerText = 'Spray 💦';
                if (modalBtn) modalBtn.disabled = false;
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
                if (document.getElementById('preview-modal') && document.getElementById('preview-modal').classList.contains('show')) {
                    closePreviewModal();
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
                if (viewMode === 'dashboard') {
                    await updateDashboardData();
                    return;
                }
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

        // Setup ended listener (restores defaults if needed)

        setInterval(autoSync, 15000);
        setInterval(() => {
            if (viewMode === 'dashboard' && !rtspEnabled) {
                refreshDashboardSnapshot();
            }
        }, 5000);
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

@app.route('/api/thumbnail/<filename>')
def serve_thumbnail(filename):
    thumb_path = os.path.join(THUMBNAILS_DIR, filename)
    if os.path.exists(thumb_path):
        return send_from_directory(THUMBNAILS_DIR, filename)
        
    original_path = None
    for d in [RAW_DIR, SQUIRREL_DIR, NOT_SQUIRREL_DIR, TRASH_DIR]:
        p = os.path.join(d, filename)
        if os.path.exists(p):
            original_path = p
            break
            
    if not original_path:
        return "Original image not found", 404
        
    try:
        from PIL import Image
        with Image.open(original_path) as img:
            w, h = img.size
            if w > 320:
                new_h = int(h * (320.0 / w))
                img = img.resize((320, new_h), Image.Resampling.LANCZOS)
            img.save(thumb_path, "JPEG", quality=85)
        return send_from_directory(THUMBNAILS_DIR, filename)
    except Exception as e:
        print("Error generating thumbnail:", e)
        return send_from_directory(os.path.dirname(original_path), filename)

@app.route('/api/next_image')
def next_image():
    mode = request.args.get('mode', 'queue')
    current = request.args.get('current', '')
    reverse = request.args.get('reverse', 'false') == 'true'
    index_str = request.args.get('index', '')
    show_current = request.args.get('show_current', 'false') == 'true'
    
    category_map = {
        'squirrel': 'squirrel',
        'not_squirrel': 'not_squirrel',
        'queue': 'raw'
    }
    db_category = category_map.get(mode, 'raw')
    
    try:
        query = db_session.query(DBImage).filter_by(category=db_category)
        if reverse:
            query = query.order_by(DBImage.filename.desc())
        else:
            query = query.order_by(DBImage.filename.asc())
        db_images = query.all()
        files = [img.filename for img in db_images]
        image_meta = {
            img.filename: {
                'confidence': img.prediction_confidence
            }
            for img in db_images
        }
    except Exception as e:
        print("Error getting images for next_image:", e)
        files = []
        image_meta = {}
        
    image = None
    current_idx = 0
    total = len(files)
    
    if total > 0:
        if index_str.isdigit():
            idx = int(index_str)
            if 0 <= idx < total:
                image = files[idx]
                current_idx = idx
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
        'confidence': image_meta.get(image, {}).get('confidence') if image else None,
        'index': current_idx,
        'total': total,
        'stats': get_stats(),
        'has_history': db_session.query(DBUndoEvent).count() > 0
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
        
    category_map = {
        'squirrel': 'squirrel',
        'not_squirrel': 'not_squirrel',
        'queue': 'raw'
    }
    db_category = category_map.get(mode, 'raw')
    
    try:
        query = db_session.query(DBImage).filter_by(category=db_category)
        if reverse:
            query = query.order_by(DBImage.filename.desc())
        else:
            query = query.order_by(DBImage.filename.asc())
            
        total_images = query.count()
        total_pages = (total_images + per_page - 1) // per_page if total_images > 0 else 1
        
        if page < 1:
            page = 1
        elif page > total_pages:
            page = total_pages
            
        offset = (page - 1) * per_page
        page_imgs = query.offset(offset).limit(per_page).all()
        page_files = [img.filename for img in page_imgs]
        image_meta = {
            img.filename: {
                'confidence': img.prediction_confidence
            }
            for img in page_imgs
        }
    except Exception as e:
        print("Error listing images from DB:", e)
        page_files = []
        image_meta = {}
        total_images = 0
        total_pages = 1
        
    return jsonify({
        'images': page_files,
        'image_meta': image_meta,
        'page': page,
        'per_page': per_page,
        'total_images': total_images,
        'total_pages': total_pages,
        'stats': get_stats(),
        'has_history': db_session.query(DBUndoEvent).count() > 0
    })

@app.route('/api/list_videos')
def list_videos():
    reverse = request.args.get('reverse', 'false') == 'true'
    
    try:
        query = db_session.query(DBVideo)
        if reverse:
            query = query.order_by(DBVideo.created_at.desc())
        else:
            query = query.order_by(DBVideo.created_at.asc())
        videos_list = query.all()
    except Exception as e:
        print("Error listing videos from DB:", e)
        videos_list = []
        
    videos = []
    classifications = {}
    favorites = {}
    false_alarm_video_count = 0
    
    for v in videos_list:
        video_path = os.path.join(VIDEOS_DIR, v.filename)
        if not os.path.exists(video_path):
            continue
        videos.append(v.filename)
        classification = get_video_event_classification(v)
        if classification:
            classifications[v.filename] = classification
            if classification == 'false_positive':
                false_alarm_video_count += 1
        if v.is_favorite:
            favorites[v.filename] = True
            
    return jsonify({
        'videos': videos,
        'classifications': classifications,
        'favorites': favorites,
        'false_alarm_video_count': false_alarm_video_count,
        'stats': get_stats(),
        'has_history': db_session.query(DBUndoEvent).count() > 0
    })

def check_drawtext_support(ffmpeg_path):
    try:
        import subprocess
        res = subprocess.run([ffmpeg_path, '-filters'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        return 'drawtext' in res.stdout
    except Exception:
        return False

def find_available_font():
    import os
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def find_retro_font():
    import os
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

@app.route('/api/compilation/<date_str>')
def get_daily_compilation(date_str):
    import re
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
        return jsonify({'status': 'error', 'message': 'Invalid date format (expected YYYY-MM-DD)'}), 400
        
    date_clean = date_str.replace('-', '') # YYYYMMDD
    
    # Query videos for this day whose linked spray event is not marked false positive.
    try:
        videos = db_session.query(DBVideo).filter(
            DBVideo.filename.like('vid_{0}_%.mp4'.format(date_clean))
        ).all()
        videos = [v for v in videos if get_video_event_classification(v) != 'false_positive']
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Database query error: ' + str(e)}), 500
        
    if not videos:
        return jsonify({'status': 'error', 'message': 'No videos found for this date.'}), 404
        
    # Sort chronologically (ascending)
    videos.sort(key=lambda v: v.filename)
    
    output_filename = 'compilation_{0}.mp4'.format(date_clean)
    output_path = os.path.join(VIDEOS_DIR, output_filename)
    
    input_paths = [os.path.join(VIDEOS_DIR, v.filename) for v in videos if os.path.exists(os.path.join(VIDEOS_DIR, v.filename))]
    if not input_paths:
        return jsonify({'status': 'error', 'message': 'No physical video files found on disk.'}), 404
        
    needs_rebuild = True
    if os.path.exists(output_path):
        out_mtime = os.path.getmtime(output_path)
        # Rebuild if any source file is newer than output
        if all(os.path.getmtime(ip) < out_mtime for ip in input_paths):
            needs_rebuild = False
            
    if needs_rebuild:
        import tempfile
        import subprocess
        
        try:
            settings = load_settings()
            ffmpeg_path = settings.get('ffmpeg_path') or shutil.which('ffmpeg') or 'ffmpeg'
            
            # Check if drawtext filter is available in the compiled FFmpeg binary
            drawtext_supported = check_drawtext_support(ffmpeg_path)
            font_path = find_available_font() if drawtext_supported else None
            retro_font_path = find_retro_font() if drawtext_supported else None
            
            temp_output_path = output_path + '.tmp'
            
            if drawtext_supported:
                # Build filter complex for watermarks + concat
                filter_parts = []
                videos_to_stitch = [v.filename for v in videos if os.path.exists(os.path.join(VIDEOS_DIR, v.filename))]
                
                for idx, filename in enumerate(videos_to_stitch):
                    # Parse date/time from vid_YYYYMMDD_HHMMSS.mp4
                    match = re.match(r'vid_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})\.mp4', filename)
                    if match:
                        year, month, day, hour, idx_m, sec = match.groups()
                        date_str = "{0}-{1}-{2}".format(year, month, day)
                        time_str = "{0}:{1}:{2}".format(hour, idx_m, sec)
                        watermark_text = "{0} {1}".format(date_str, time_str)
                    else:
                        watermark_text = filename
                        
                    # Video game high score style blast counter (e.g. BLAST: 01)
                    score_text = "BLAST: {0:02d}".format(idx + 1)
                    
                    # Escape special characters for ffmpeg filter parsing
                    escaped_text = watermark_text.replace("'", "'\\''").replace(":", "\\:")
                    escaped_score = score_text.replace("'", "'\\''").replace(":", "\\:")
                    
                    # Bottom-left Date/Time overlay
                    if font_path:
                        escaped_font = font_path.replace(":", "\\:").replace("'", "'\\''")
                        left_filter = "drawtext=text='{0}':fontfile='{1}':fontsize=20:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=6:x=20:y=h-36".format(escaped_text, escaped_font)
                    else:
                        left_filter = "drawtext=text='{0}':fontsize=20:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=6:x=20:y=h-36".format(escaped_text)
                        
                    # Top-right Arcade Blast Counter overlay
                    if retro_font_path:
                        escaped_retro = retro_font_path.replace(":", "\\:").replace("'", "'\\''")
                        right_filter = "drawtext=text='{0}':fontfile='{1}':fontsize=36:fontcolor=0xFACC15:borderw=3:bordercolor=black:x=w-tw-20:y=20".format(escaped_score, escaped_retro)
                    else:
                        right_filter = "drawtext=text='{0}':fontsize=36:fontcolor=0xFACC15:borderw=3:bordercolor=black:x=w-tw-20:y=20".format(escaped_score)
                        
                    # Chain the left and right overlays together
                    filter_parts.append("[{0:d}:v]{1}, {2}[v{0:d}]".format(idx, left_filter, right_filter))
                
                if len(videos_to_stitch) > 1:
                    concat_inputs = "".join(["[v{0}]".format(i) for i in range(len(videos_to_stitch))])
                    filter_parts.append("{0}concat=n={1}:v=1:a=0[outv]".format(concat_inputs, len(videos_to_stitch)))
                    map_output = "[outv]"
                else:
                    map_output = "[v0]"
                    
                filter_complex_str = "; ".join(filter_parts)
                
                cmd = [ffmpeg_path, '-y']
                for filename in videos_to_stitch:
                    cmd.extend(['-i', os.path.join(VIDEOS_DIR, filename)])
                cmd.extend([
                    '-filter_complex', filter_complex_str,
                    '-map', map_output,
                    '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart',
                    '-f', 'mp4',
                    temp_output_path
                ])
                
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
            else:
                # Fallback to simple demuxer concat if drawtext is not supported
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f_list:
                    for ip in input_paths:
                        escaped_path = ip.replace("'", "'\\''")
                        f_list.write("file '{0}'\n".format(escaped_path))
                    list_filename = f_list.name
                    
                cmd = [
                    ffmpeg_path, '-y',
                    '-f', 'concat',
                    '-safe', '0',
                    '-i', list_filename,
                    '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart',
                    '-f', 'mp4',
                    temp_output_path
                ]
                
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if os.path.exists(list_filename):
                    os.remove(list_filename)
                
            if res.returncode != 0:
                if os.path.exists(temp_output_path):
                    try:
                        os.remove(temp_output_path)
                    except Exception:
                        pass
                error_msg = res.stderr.decode('utf-8', errors='ignore')
                log_message("[Compilation] FFmpeg error: {0}".format(error_msg))
                return jsonify({'status': 'error', 'message': 'FFmpeg concatenation failed: ' + error_msg}), 500
                
            os.rename(temp_output_path, output_path)
            log_message("[Compilation] Stitched video created: {0}".format(output_filename))
        except Exception as e:
            if os.path.exists(temp_output_path):
                try:
                    os.remove(temp_output_path)
                except Exception:
                    pass
            return jsonify({'status': 'error', 'message': 'Error creating compilation: ' + str(e)}), 500
            
    return jsonify({
        'status': 'success',
        'url': '/video/{0}'.format(output_filename),
        'filename': output_filename
    })

@app.route('/api/delete_video', methods=['POST'])
def delete_video():
    data = request.get_json() or {}
    filename = data.get('filename')
    if not filename:
        return jsonify({'status': 'error', 'message': 'Missing filename'}), 400
        
    try:
        db_video = db_session.query(DBVideo).filter_by(filename=filename).first()
        false_alarm_training = None
        if db_video and get_video_event_classification(db_video) == 'false_positive':
            false_alarm_training = extract_false_alarm_training_frames(filename)
        deleted_files = delete_video_files(filename)
        if db_video or deleted_files > 0:
            return jsonify({
                'status': 'success',
                'deleted_files': deleted_files,
                'false_alarm_training': false_alarm_training,
                'stats': get_stats(),
                'has_history': db_session.query(DBUndoEvent).count() > 0
            })
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Failed to delete video file: ' + str(e)}), 500
        
    return jsonify({'status': 'error', 'message': 'Video not found'}), 404

@app.route('/api/delete_false_alarm_videos', methods=['POST'])
def delete_false_alarm_videos():
    try:
        videos = [
            video for video in db_session.query(DBVideo).all()
            if get_video_event_classification(video) == 'false_positive'
        ]
        deleted_files = 0
        deleted_videos = 0
        false_alarm_training = {
            'processed': 0,
            'missing': 0,
            'created': 0,
            'total_false_alarms': len(videos)
        }
        for video in videos:
            result = extract_false_alarm_training_frames(video.filename)
            false_alarm_training['created'] += result.get('created', 0)
            if result.get('reason') == 'missing_video':
                false_alarm_training['missing'] += 1
            else:
                false_alarm_training['processed'] += 1
            removed = delete_video_files(video.filename)
            if removed > 0:
                deleted_videos += 1
                deleted_files += removed
        return jsonify({
            'status': 'success',
            'deleted_videos': deleted_videos,
            'deleted_files': deleted_files,
            'false_alarm_training': false_alarm_training,
            'stats': get_stats(),
            'has_history': db_session.query(DBUndoEvent).count() > 0
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Failed to delete false alarm videos: ' + str(e)}), 500

@app.route('/api/classify_video', methods=['POST'])
def classify_video():
    data = request.get_json() or {}
    video_name = data.get('video_name')
    classification = data.get('classification') # 'accurate', 'false_positive', or null
    
    if not video_name:
        return jsonify({'status': 'error', 'message': 'Missing video_name'}), 400
        
    if classification not in [None, 'accurate', 'false_positive']:
        return jsonify({'status': 'error', 'message': 'Invalid classification'}), 400
        
    try:
        db_vid = db_session.query(DBVideo).filter_by(filename=video_name).first()
        if db_vid:
            set_video_event_classification(db_vid, classification)
        else:
            video_time = get_video_timestamp(video_name)
            created_at = video_time if video_time else datetime.datetime.now()
            db_vid = DBVideo(
                filename=video_name,
                blast_id=None,
                is_favorite=False,
                classification=classification,
                created_at=created_at
            )
            db_session.add(db_vid)
            db_session.flush()
            set_video_event_classification(db_vid, classification)
        db_session.commit()
        false_alarm_training = None
        if classification == 'false_positive':
            false_alarm_training = extract_false_alarm_training_frames(video_name)
            if false_alarm_training.get('created', 0) > 0:
                log_message("[False Alarm Training] Added {0} hard-negative frames from {1}".format(
                    false_alarm_training['created'], video_name
                ))
        return jsonify({
            'status': 'success',
            'stats': get_stats(),
            'has_history': db_session.query(DBUndoEvent).count() > 0,
            'false_alarm_training': false_alarm_training
        })
    except Exception as e:
        db_session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/favorite_video', methods=['POST'])
def favorite_video():
    data = request.get_json() or {}
    video_name = data.get('video_name')
    favorite = data.get('favorite', False) # True or False
    
    if not video_name:
        return jsonify({'status': 'error', 'message': 'Missing video_name'}), 400
        
    try:
        db_vid = db_session.query(DBVideo).filter_by(filename=video_name).first()
        if db_vid:
            db_vid.is_favorite = favorite
        else:
            video_time = get_video_timestamp(video_name)
            created_at = video_time if video_time else datetime.datetime.now()
            db_vid = DBVideo(
                filename=video_name,
                blast_id=None,
                is_favorite=favorite,
                classification=None,
                created_at=created_at
            )
            db_session.add(db_vid)
        db_session.commit()
        return jsonify({
            'status': 'success',
            'stats': get_stats(),
            'has_history': db_session.query(DBUndoEvent).count() > 0
        })
    except Exception as e:
        db_session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
def get_available_models():
    models = []
    if os.path.exists(MODELS_DIR):
        try:
            for f in os.listdir(MODELS_DIR):
                if f.endswith('.pt') or f.endswith('.pth'):
                    models.append(f)
        except Exception as e:
            log_message("Error scanning models directory: {0}".format(e))
    models.sort()
    return models

def get_model_accuracies():
    try:
        results = db_session.query(DBBlast).filter(DBBlast.type == 'auto').all()
    except Exception as e:
        print("Error querying blasts for model accuracies:", e)
        results = []
        
    model_accuracies = {}
    for b in results:
        model_key = b.model_name or 'unknown'
        if model_key not in model_accuracies:
            model_accuracies[model_key] = {
                'total': 0,
                'accurate': 0,
                'false_positive': 0,
                'accuracy_rate': None
            }
        model_accuracies[model_key]['total'] += 1
        if b.classification:
            if b.classification == 'accurate':
                model_accuracies[model_key]['accurate'] += 1
            elif b.classification == 'false_positive':
                model_accuracies[model_key]['false_positive'] += 1
                
    for model_key, stats in model_accuracies.items():
        total_classified = stats['accurate'] + stats['false_positive']
        stats['accuracy_rate'] = round((stats['accurate'] / total_classified) * 100, 1) if total_classified > 0 else None
        
    return model_accuracies

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'POST':
        data = request.get_json() or {}
        settings = load_settings()
        old_model = settings.get('active_model')
        try:
            for k in default_settings.keys():
                if k in data:
                    if isinstance(default_settings[k], bool):
                        settings[k] = setting_enabled(data[k])
                    elif isinstance(default_settings[k], int):
                        settings[k] = int(data[k])
                    elif isinstance(default_settings[k], float):
                        settings[k] = float(data[k])
                    else:
                        settings[k] = data[k]
            save_settings(settings)
            
            # If active model changed, load the new active model
            new_model = settings.get('active_model')
            if new_model != old_model:
                load_active_model()
            
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
            
            return jsonify({
                'status': 'success',
                'settings': settings,
                'available_models': get_available_models(),
                'model_accuracies': get_model_accuracies()
            })
        except Exception as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400
    else:
        return jsonify({
            'status': 'success',
            'settings': load_settings(),
            'available_models': get_available_models(),
            'model_accuracies': get_model_accuracies()
        })

@app.route('/api/settings/save_model', methods=['POST'])
def save_model_checkpoint():
    try:
        data = request.get_json() or {}
        name = data.get('name')
        if not name:
            return jsonify({'status': 'error', 'message': 'No name provided'}), 400
            
        import re
        name_clean = name.strip()
        if name_clean.endswith('.pth'):
            name_clean = name_clean[:-4]
            
        if not re.match(r'^[a-zA-Z0-9_-]+$', name_clean):
            return jsonify({'status': 'error', 'message': 'Invalid characters in name. Use alphanumeric, dashes, and underscores.'}), 400
            
        src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'model.pth')
        if not os.path.exists(src_path):
            return jsonify({'status': 'error', 'message': 'No trained model.pth found in root directory.'}), 404
            
        dst_filename = "{0}.pth".format(name_clean)
        dst_path = os.path.join(MODELS_DIR, dst_filename)
        
        shutil.copy2(src_path, dst_path)
        log_message("Saved custom model checkpoint: {0}".format(dst_filename))
        
        return jsonify({
            'status': 'success',
            'message': 'Checkpoint saved successfully.',
            'filename': dst_filename,
            'available_models': get_available_models()
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/blasts')
def get_blasts():
    try:
        blasts_query = db_session.query(DBBlast).order_by(DBBlast.timestamp.desc()).all()
        videos_query = db_session.query(DBVideo).all()
        videos_by_blast_id = {v.blast_id: v for v in videos_query if v.blast_id}
        videos_by_filename = {v.filename: v for v in videos_query}
    except Exception as e:
        print("Error querying blasts:", e)
        blasts_query = []
        videos_by_blast_id = {}
        videos_by_filename = {}
        
    blasts = []
    accurate_count = 0
    false_positive_count = 0
    auto_blasts_count = 0
    manual_blasts_count = 0
    
    for b in blasts_query:
        v = videos_by_blast_id.get(b.id)
        if not v and b.video_filename:
            v = videos_by_filename.get(b.video_filename)
        video_filename = v.filename if v else b.video_filename
        blast_dict = {
            'timestamp': b.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            'epoch': b.timestamp.timestamp(),
            'type': b.type,
            'confidence': b.confidence,
            'duration': b.duration,
            'video_filename': video_filename,
            'event_id': b.id
        }
        
        if b.classification:
            blast_dict['classification'] = b.classification
            if b.classification == 'accurate':
                accurate_count += 1
            elif b.classification == 'false_positive':
                false_positive_count += 1
        if v:
            if v.is_favorite:
                blast_dict['favorite'] = True
                
        blasts.append(blast_dict)
        
        if b.type == 'auto':
            auto_blasts_count += 1
        elif b.type == 'manual':
            manual_blasts_count += 1
            
    # Missed squirrels calculation:
    # Any image classified as 'squirrel' that does NOT have an associated blast within 30 seconds.
    missed_squirrels = []
    try:
        squirrel_imgs = db_session.query(DBImage).filter_by(category='squirrel', is_auto_classified=False).all()
        for img in squirrel_imgs:
            has_blast = False
            for b in blasts_query:
                diff = abs((img.captured_at - b.timestamp).total_seconds())
                if diff <= 30.0:
                    has_blast = True
                    break
            if not has_blast:
                missed_squirrels.append({
                    'timestamp': img.captured_at.strftime("%Y-%m-%d %H:%M:%S"),
                    'epoch': img.captured_at.timestamp()
                })
    except Exception as e:
        print("Error calculating missed squirrels:", e)
        
    total_classified = accurate_count + false_positive_count
    accuracy_rate = round((accurate_count / total_classified) * 100, 1) if total_classified > 0 else None
            
    return jsonify({
        'blasts': blasts,
        'missed_squirrels': missed_squirrels,
        'total_blasts': len(blasts),
        'auto_blasts': auto_blasts_count,
        'manual_blasts': manual_blasts_count,
        'classified_accurate': accurate_count,
        'classified_false_positive': false_positive_count,
        'accuracy_rate': accuracy_rate,
        'model_accuracies': get_model_accuracies(),
        'available_models': get_available_models()
    })

@app.route('/api/latest_image')
def latest_image():
    # Prefer the latest analyzed frame. In still-photo mode most frames are not
    # saved for review, but the live dashboard should still reflect every analysis.
    with frame_lock:
        img_bytes = latest_frame_jpeg
    if img_bytes is not None:
        from flask import make_response
        response = make_response(img_bytes)
        response.headers.set('Content-Type', 'image/jpeg')
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response

    try:
        latest_img = db_session.query(DBImage).order_by(DBImage.captured_at.desc()).first()
        if latest_img:
            dir_map = {
                'raw': RAW_DIR,
                'squirrel': SQUIRREL_DIR,
                'not_squirrel': NOT_SQUIRREL_DIR,
                'trash': TRASH_DIR
            }
            target_dir = dir_map.get(latest_img.category)
            if target_dir and os.path.exists(os.path.join(target_dir, latest_img.filename)):
                response = send_from_directory(target_dir, latest_img.filename)
                response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
                return response
    except Exception as e:
        print("Error getting latest image from DB:", e)
    return "No image found", 404

@app.route('/api/live_stream')
def live_stream():
    from flask import Response
    def gen():
        global latest_frame_jpeg
        last_sent = None
        while True:
            with frame_lock:
                current_jpeg = latest_frame_jpeg
            
            if current_jpeg is None or current_jpeg == last_sent:
                time.sleep(0.05)
                continue
                
            last_sent = current_jpeg
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + current_jpeg + b'\r\n\r\n')
            time.sleep(0.05)
    return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/classify', methods=['POST'])
def classify():
    data = request.get_json()
    filename = data.get('filename')
    category = data.get('category')
    
    if not filename or not category:
        return jsonify({'status': 'error', 'message': 'Missing filename or category'}), 400
        
    newly_added = False
    try:
        db_img = db_session.query(DBImage).filter_by(filename=filename).first()
        if not db_img:
            src_path = None
            src_dir = None
            src_category = None
            for d, cat in [(RAW_DIR, 'raw'), (SQUIRREL_DIR, 'squirrel'), (NOT_SQUIRREL_DIR, 'not_squirrel'), (TRASH_DIR, 'trash')]:
                p = os.path.join(d, filename)
                if os.path.exists(p):
                    src_path = p
                    src_dir = d
                    src_category = cat
                    break
            if not src_path:
                return jsonify({'status': 'error', 'message': 'Image does not exist'}), 404
            
            mtime = os.path.getmtime(src_path)
            captured_at = get_image_timestamp(filename) or datetime.datetime.fromtimestamp(mtime)
            db_img = DBImage(
                filename=filename,
                category=src_category,
                captured_at=captured_at
            )
            db_session.add(db_img)
            db_session.flush()
            newly_added = True
    except Exception as e:
        db_session.rollback()
        return jsonify({'status': 'error', 'message': 'Database error: ' + str(e)}), 500
        
    category_map = {
        'squirrel': (SQUIRREL_DIR, 'squirrel'),
        'not_squirrel': (NOT_SQUIRREL_DIR, 'not_squirrel'),
        'delete': (TRASH_DIR, 'trash'),
        'trash': (TRASH_DIR, 'trash'),
        'raw': (RAW_DIR, 'raw')
    }
    
    if category not in category_map:
        return jsonify({'status': 'error', 'message': 'Invalid category'}), 400
        
    target_dir, db_target_category = category_map[category]
    
    dir_map = {
        'raw': RAW_DIR,
        'squirrel': SQUIRREL_DIR,
        'not_squirrel': NOT_SQUIRREL_DIR,
        'trash': TRASH_DIR
    }
    src_dir = dir_map.get(db_img.category)
    
    if src_dir == target_dir:
        if newly_added:
            try:
                db_session.commit()
            except Exception as e:
                db_session.rollback()
                return jsonify({'status': 'error', 'message': 'Database error: ' + str(e)}), 500
        return jsonify({'status': 'success', 'stats': get_stats(), 'has_history': db_session.query(DBUndoEvent).count() > 0})
        
    src_path = os.path.join(src_dir, filename)
    target_path = os.path.join(target_dir, filename)
    
    try:
        if os.path.exists(src_path):
            shutil.move(src_path, target_path)
            
        undo_ev = DBUndoEvent(
            timestamp=datetime.datetime.now(),
            filename=filename,
            original_category=db_img.category,
            target_category=db_target_category
        )
        db_session.add(undo_ev)
        
        db_img.category = db_target_category
        db_img.is_auto_classified = False
        db_session.commit()
        
        return jsonify({
            'status': 'success',
            'stats': get_stats(),
            'has_history': True
        })
    except Exception as e:
        db_session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/undo', methods=['POST'])
def undo():
    try:
        last_action = db_session.query(DBUndoEvent).order_by(DBUndoEvent.timestamp.desc()).first()
        if not last_action:
            return jsonify({'status': 'error', 'message': 'No actions to undo'}), 400
            
        filename = last_action.filename
        original_category = last_action.original_category
        target_category = last_action.target_category
        
        category_dirs = {
            'raw': RAW_DIR,
            'squirrel': SQUIRREL_DIR,
            'not_squirrel': NOT_SQUIRREL_DIR,
            'trash': TRASH_DIR
        }
        
        src_dir = category_dirs.get(target_category)
        dest_dir = category_dirs.get(original_category)
        
        if not src_dir or not dest_dir:
            return jsonify({'status': 'error', 'message': 'Invalid undo categories'}), 500
            
        src_path = os.path.join(src_dir, filename)
        dest_path = os.path.join(dest_dir, filename)
        
        if os.path.exists(src_path):
            shutil.move(src_path, dest_path)
            
        db_img = db_session.query(DBImage).filter_by(filename=filename).first()
        if db_img:
            db_img.category = original_category
            db_img.is_auto_classified = filename.startswith('img_auto_')
            
        db_session.delete(last_action)
        db_session.commit()
        
        return jsonify({
            'status': 'success',
            'undone_image': filename,
            'stats': get_stats(),
            'has_history': db_session.query(DBUndoEvent).count() > 0
        })
    except Exception as e:
        db_session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/sync', methods=['POST'])
def sync():
    if not sync_lock.acquire(False):
        return jsonify({
            'status': 'success',
            'output': 'Sync already running; skipped overlapping request.',
            'stats': get_stats()
        })

    data = request.get_json(silent=True) or {}
    use_gemini = data.get('auto_label', False)
    settings = load_settings()
    enable_rtsp = settings.get('enable_rtsp', True)
    
    try:
        res_stdout = ""
        if not enable_rtsp:
            # Trigger the Pi to push its backlog
            pi_sync_url = 'http://{0}:8080/sync'.format(PI_IP)
            log_message("[Sync] Triggering Pi to push backlog files: {0}".format(pi_sync_url))
            pi_sync_success = False
            pi_sync_error = None
            
            try:
                import urllib.request
                req = urllib.request.Request(pi_sync_url, method='POST')
                with urllib.request.urlopen(req, timeout=5) as response:
                    response.read()
                pi_sync_success = True
                log_message("[Sync] Pi backlog push triggered successfully.")
            except Exception as pe:
                pi_sync_error = str(pe)
                log_message("[Sync] Pi push sync failed or Pi offline: {0}".format(pe))
                
            # Fallback to local sync_images.sh pull script if Pi trigger fails (e.g. running outside Docker or Pi trigger server offline)
            if not pi_sync_success:
                is_docker = os.path.exists('/.dockerenv') or os.environ.get('RUNNING_IN_DOCKER') == 'true'
                if is_docker:
                    log_message("[Sync] Pi push sync failed/offline and running inside Docker. Cannot fallback to local pull ssh (hostname/keys unavailable).")
                    raise Exception("Pi trigger sync failed: {0}. Please ensure trigger_server.py is updated on the Pi and the Pi is online.".format(pi_sync_error))
                    
                log_message("[Sync] Falling back to local pull sync via sync_images.sh...")
                script_path = os.path.join(BASE_DIR, 'sync_images.sh')
                if os.path.exists(script_path):
                    res = subprocess.run([script_path], capture_output=True, text=True, check=True)
                    res_stdout = res.stdout
                    log_message("[Sync] Fallback pull sync completed successfully.")
                else:
                    log_message("[Sync] Fallback pull sync failed: sync_images.sh not found.")
                    raise Exception("Pi trigger sync failed: {0}. Local pull script sync_images.sh not found.".format(pi_sync_error))
            else:
                res_stdout = "Pi backlog push sync triggered successfully."
                # Sleep 1.5 seconds to let first files start transferring before indexing
                time.sleep(1.5)
        else:
            res_stdout = "RTSP streaming active; local file sync skipped."
        
        process_synced_videos()
        
        if use_gemini:
            python_executable = os.path.join(BASE_DIR, '.venv', 'bin', 'python3')
            labeler_script = os.path.join(BASE_DIR, 'auto_label.py')
            settings = load_settings()
            env = os.environ.copy()
            if settings.get('gemini_api_key'):
                env['GEMINI_API_KEY'] = settings['gemini_api_key']
            subprocess.run([python_executable, labeler_script], env=env, check=True)
        
        sync_db_with_filesystem()
        
        return jsonify({
            'status': 'success',
            'output': res_stdout,
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
    finally:
        sync_lock.release()

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
                if settings.get('enable_rtsp', True):
                    start_local_video_recording(duration, "manual_spray")
            return jsonify(json.loads(res_data))
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/spray_confirm', methods=['POST'])
def spray_confirm():
    global last_spray_time
    data = request.get_json(silent=True) or {}
    confidence = data.get('confidence')
    try:
        confidence = float(confidence) if confidence is not None else None
    except Exception:
        confidence = None

    duration = data.get('duration')
    try:
        duration = float(duration) if duration is not None else None
    except Exception:
        duration = None

    image_filename = data.get('image_filename')
    if image_filename and os.path.basename(image_filename) != image_filename:
        image_filename = None

    model_name = data.get('model_name') or active_model_name

    try:
        log_blast('auto', confidence, model_name, image_filename, duration=duration)
        last_spray_time = time.time()
        log_message("[Spray Confirm] Pi confirmed automatic spray. confidence={0}, image={1}".format(
            "{0:.1f}%".format(confidence * 100) if confidence is not None else "n/a",
            image_filename or "none"
        ))
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/automation_status')
def get_automation_status():
    global automation_enabled
    return jsonify({'enabled': automation_enabled})

@app.route('/api/pi_status', methods=['POST'])
def pi_status():
    global latest_pi_status
    data = request.get_json(silent=True) or {}
    data['received_at'] = time.time()
    with telemetry_lock:
        latest_pi_status = data
        add_health_sample('pi', pi=data, predict=data.get('server_metrics', {}))
    return jsonify({'status': 'success'})

@app.route('/api/health')
def api_health():
    settings = load_settings()
    with telemetry_lock:
        pi_status_copy = dict(latest_pi_status)
        predict_metrics_copy = dict(latest_predict_metrics)
    with frame_lock:
        frame_age = time.time() - latest_frame_time if latest_frame_time else None
        latest_frame_size = len(latest_frame_jpeg) if latest_frame_jpeg else 0

    status = 'ok'
    if frame_age is None or frame_age > 300:
        status = 'offline'
    elif frame_age > max(60, int(settings.get('analysis_interval', 5)) * 4):
        status = 'stale'

    return jsonify({
        'status': status,
        'automation_enabled': automation_enabled,
        'active_model': active_model_name,
        'active_model_type': active_model_type,
        'latest_frame_age_seconds': frame_age,
        'latest_frame_size_bytes': latest_frame_size,
        'last_spray_age_seconds': time.time() - last_spray_time if last_spray_time else None,
        'pi': pi_status_copy,
        'predict': predict_metrics_copy,
        'settings': {
            'analysis_interval': settings.get('analysis_interval'),
            'save_interval': settings.get('save_interval'),
            'analysis_width': settings.get('analysis_width'),
            'analysis_height': settings.get('analysis_height'),
            'analysis_jpeg_quality': settings.get('analysis_jpeg_quality'),
            'review_jpeg_quality': settings.get('review_jpeg_quality'),
            'motion_prefilter_enabled': settings.get('motion_prefilter_enabled'),
            'motion_threshold': settings.get('motion_threshold'),
            'motion_force_interval': settings.get('motion_force_interval'),
            'daylight_mode': settings.get('daylight_mode'),
            'daylight_latitude': settings.get('daylight_latitude'),
            'daylight_longitude': settings.get('daylight_longitude'),
            'sunrise_offset_minutes': settings.get('sunrise_offset_minutes'),
            'sunset_offset_minutes': settings.get('sunset_offset_minutes'),
            'daylight_start_hour': settings.get('daylight_start_hour'),
            'daylight_end_hour': settings.get('daylight_end_hour'),
            'camera_roi': settings.get('camera_roi'),
            'video_roi': settings.get('video_roi'),
            'camera_rotation': settings.get('camera_rotation'),
            'confidence_threshold': settings.get('confidence_threshold'),
            'spray_decision_required_hits': settings.get('spray_decision_required_hits'),
            'spray_decision_window_seconds': settings.get('spray_decision_window_seconds'),
            'spray_decision_average_confidence': settings.get('spray_decision_average_confidence')
        }
    })

@app.route('/api/health/history')
def api_health_history():
    since_seconds = request.args.get('seconds', default=600, type=int)
    cutoff = time.time() - max(30, min(since_seconds, 86400))
    with telemetry_lock:
        samples = [s for s in list(health_history) if s.get('t', 0) >= cutoff]
    return jsonify({
        'status': 'success',
        'samples': samples
    })

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
    false_alarm_training = extract_all_false_alarm_training_frames()
    try:
        with open(log_path, 'w') as f:
            f.write("Initializing local retraining...\n")
            f.write("False alarm hard negatives: created {0} frames from {1} available videos ({2} missing/deleted videos skipped).\n".format(
                false_alarm_training['created'],
                false_alarm_training['processed'],
                false_alarm_training['missing']
            ))
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
            stdout=open(log_path, 'a'),
            stderr=subprocess.STDOUT
        )
        log_message("[Training] Started background training subprocess (PID: {0})".format(training_process.pid))
        return jsonify({
            'status': 'success',
            'message': 'Training started.',
            'false_alarm_training': false_alarm_training
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/train/false_alarms', methods=['POST'])
def train_false_alarms():
    try:
        result = extract_all_false_alarm_training_frames()
        log_message("[False Alarm Training] Backfill complete: created {0} frames from {1} videos; skipped {2} missing/deleted videos.".format(
            result['created'], result['processed'], result['missing']
        ))
        return jsonify({'status': 'success', 'result': result, 'stats': get_stats()})
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
                    src_path = os.path.join(BASE_DIR, 'model.pth')
                    dst_path = os.path.join(MODELS_DIR, 'resnet18_default.pth')
                    if os.path.exists(src_path):
                        shutil.copy2(src_path, dst_path)
                        log_message("[Training] Copied newly trained model.pth to data/models/resnet18_default.pth")
                    load_active_model()
                    log_message("[Training] Hot-reloaded active model successfully.")
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

@app.route('/api/upload_video', methods=['POST'])
def upload_video():
    filename = request.args.get('filename')
    if not filename:
        return jsonify({'status': 'error', 'message': 'Missing filename'}), 400
    
    filepath = os.path.join(RAW_DIR, filename)
    try:
        with open(filepath, 'wb') as f:
            f.write(request.data)
        log_message("[Video Upload] Received raw video {0} from Pi ({1} bytes)".format(filename, len(request.data)))
        process_synced_videos()
        return jsonify({'status': 'success'})
    except Exception as e:
        log_message("Error receiving video {0}: {1}".format(filename, str(e)))
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/predict', methods=['POST'])
def predict():
    global automation_enabled, last_spray_time
    global latest_frame_jpeg, latest_frame_time, latest_predict_metrics
    request_started_at = time.time()
    img_data = request.data
    if not img_data:
        return jsonify({'status': 'error', 'message': 'No image data received'}), 400
        
    is_test = request.args.get('test') == 'true'
    save_requested = setting_enabled(request.args.get('save', 'true'))
    settings = load_settings()
    
    import datetime
    now_dt = datetime.datetime.now()
    filename = "img_auto_{0}.jpg".format(now_dt.strftime("%Y%m%d_%H%M%S_%f"))
    temp_filename = "_temp_predict_{0}.jpg".format(now_dt.strftime("%Y%m%d_%H%M%S_%f"))
    filepath = os.path.join(RAW_DIR, filename) if save_requested else os.path.join(PREDICT_TMP_DIR, temp_filename)
    
    write_started_at = time.time()
    with open(filepath, 'wb') as f:
        f.write(img_data)
    write_ms = (time.time() - write_started_at) * 1000

    normalize_started_at = time.time()
    live_frame_jpeg = make_live_frame_jpeg(img_data, settings)
    normalize_ms = (time.time() - normalize_started_at) * 1000
    with frame_lock:
        latest_frame_jpeg = live_frame_jpeg
        latest_frame_time = time.time()

    predict_started_at = time.time()
    is_squirrel, confidence = model_predict(filepath)
    model_ms = (time.time() - predict_started_at) * 1000
            
    current_time = time.time()
    threshold = float(settings.get('confidence_threshold', 0.70))
    cooldown = float(settings.get('spray_cooldown_seconds', 60))
    cooldown_active = (current_time - last_spray_time < cooldown)
    meets_threshold = (confidence >= threshold)
    decision = get_spray_decision(is_squirrel, confidence, settings, current_time)
    should_spray = automation_enabled and decision['ready'] and not cooldown_active

    should_save_image = save_requested or should_spray
    db_img = None

    save_started_at = time.time()
    if should_save_image:
        try:
            db_img = db_session.query(DBImage).filter_by(filename=filename).first()
            if not db_img:
                db_img = DBImage(
                    filename=filename,
                    category='raw',
                    captured_at=now_dt,
                    prediction_confidence=confidence,
                    is_auto_classified=False
                )
                db_session.add(db_img)
                db_session.flush()
            else:
                db_img.prediction_confidence = confidence
                db_session.flush()
        except Exception as e:
            db_session.rollback()
            db_img = None
            print("Error saving image to DB during predict:", e)

        if confidence > 0.85:
            target_category = 'squirrel' if is_squirrel else 'not_squirrel'
            target_dir = SQUIRREL_DIR if is_squirrel else NOT_SQUIRREL_DIR
            try:
                shutil.move(filepath, os.path.join(target_dir, filename))
                if db_img:
                    db_img.category = target_category
                    db_img.is_auto_classified = True
                    db_session.commit()
                    log_message("[Auto-Classify] Automatically classified {0} as {1} (confidence: {2:.2f})".format(
                        filename, 'squirrel' if is_squirrel else 'not_squirrel', confidence
                    ))
                else:
                    log_message("[Auto-Classify] File moved on disk but DB record missing/failed for {0}".format(filename))
            except Exception as e:
                db_session.rollback()
                log_message("Error auto-classifying {0}: {1}".format(filename, str(e)))
        else:
            try:
                if not save_requested and os.path.exists(filepath):
                    raw_path = os.path.join(RAW_DIR, filename)
                    shutil.move(filepath, raw_path)
                    filepath = raw_path
                if db_img:
                    db_session.commit()
            except Exception as e:
                db_session.rollback()
                print("Error committing raw image to DB:", e)
    else:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception as e:
            print("Error removing unsaved prediction temp file:", e)
    save_ms = (time.time() - save_started_at) * 1000
        
    duration = get_current_spray_duration()
    
    if should_spray:
        if not is_test:
            log_message("[Spray Decision] Spray requested after {0}/{1} detections in {2:.0f}s, avg confidence {3:.1f}%. Waiting for Pi trigger confirmation.".format(
                decision['hits'],
                decision['required_hits'],
                decision['window_seconds'],
                decision['average_confidence'] * 100
            ))
    elif is_squirrel and automation_enabled:
        if not meets_threshold:
            log_message("[Prediction] Squirrel detected, but skipping spray because confidence ({0:.1f}%) is below threshold ({1:.1f}%).".format(
                confidence * 100, threshold * 100
            ))
        elif cooldown_active:
            log_message("[Cooldown] Squirrel detected, but skipping spray because cooldown is active ({0:.1f}s remaining).".format(
                cooldown - (current_time - last_spray_time)
            ))
        elif not decision['ready']:
            log_message("[Spray Decision] Detection held for confirmation: {0}/{1} hits in {2:.0f}s, avg {3:.1f}%.".format(
                decision['hits'],
                decision['required_hits'],
                decision['window_seconds'],
                decision['average_confidence'] * 100
            ))
        
    total_ms = (time.time() - request_started_at) * 1000
    metrics = {
        'received_at': time.time(),
        'filename': filename if should_save_image else None,
        'input_bytes': len(img_data),
        'live_bytes': len(live_frame_jpeg) if live_frame_jpeg else 0,
        'save_requested': save_requested,
        'saved': should_save_image,
        'is_squirrel_raw': is_squirrel,
        'is_squirrel_response': should_spray,
        'should_spray': should_spray,
        'spray_decision': decision,
        'confidence': confidence,
        'write_ms': round(write_ms, 1),
        'normalize_ms': round(normalize_ms, 1),
        'model_ms': round(model_ms, 1),
        'save_ms': round(save_ms, 1),
        'total_ms': round(total_ms, 1)
    }
    with telemetry_lock:
        latest_predict_metrics = metrics

    return jsonify({
        'is_squirrel': should_spray,
        'detected_squirrel': is_squirrel,
        'should_spray': should_spray,
        'confidence': confidence,
        'filename': filename if should_save_image else None,
        'saved': should_save_image,
        'metrics': metrics,
        'spray_decision': decision,
        'automation_enabled': automation_enabled,
        'active_model': active_model_name,
        'spray_duration': duration
    })
load_active_model()

# --- RTSP Continuous Streaming & Real-Time Inference ---
latest_frame_jpeg = None
latest_frame_time = None
latest_frame_raw = None
frame_lock = threading.Lock()

record_file_path = None
record_video_writer = None
record_until_time = 0.0
record_frames_written = 0
record_lock = threading.Lock()

def get_eastern_time():
    import datetime
    utc_now = datetime.datetime.utcnow()
    try:
        year = utc_now.year
        march_1 = datetime.datetime(year, 3, 1)
        w_march = march_1.weekday()
        first_sun_march = 1 + (6 - w_march) % 7
        second_sun_march = first_sun_march + 7
        dst_start = datetime.datetime(year, 3, second_sun_march, 2, 0, 0)
        
        nov_1 = datetime.datetime(year, 11, 1)
        w_nov = nov_1.weekday()
        first_sun_nov = 1 + (6 - w_nov) % 7
        dst_end = datetime.datetime(year, 11, first_sun_nov, 2, 0, 0)
        
        utc_start = dst_start + datetime.timedelta(hours=5)
        utc_end = dst_end + datetime.timedelta(hours=4)
        
        if utc_start <= utc_now < utc_end:
            offset = 4
        else:
            offset = 5
    except Exception:
        if 4 <= utc_now.month <= 10:
            offset = 4
        else:
            offset = 5
    return utc_now - datetime.timedelta(hours=offset)

def trigger_solenoid_on_pi(duration):
    import urllib.request
    import urllib.parse
    try:
        settings = load_settings()
        rotation = settings.get('camera_rotation', 0)
        roi = settings.get('video_roi', '')
        encoded_roi = urllib.parse.quote(roi) if roi else ''
        url = 'http://{}:8080/spray?duration={}&rotation={}&roi={}'.format(PI_IP, duration, rotation, encoded_roi)
        req = urllib.request.Request(url, method='POST')
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()
        log_message("[RTSP-Trigger] Sent spray request to Pi successfully.")
    except Exception as e:
        log_message("[RTSP-Trigger] Error triggering solenoid on Pi: {}".format(e))

def finalize_video_recording(filepath):
    try:
        import shutil
        import subprocess
        ffmpeg_path = shutil.which('ffmpeg')
        if ffmpeg_path:
            mp4_filename = os.path.basename(filepath)
            thumb_filename = os.path.splitext(mp4_filename)[0] + '.jpg'
            thumb_path = os.path.join(VIDEOS_DIR, thumb_filename)
            temp_thumb_path = thumb_path + '.tmp'
            
            thumb_cmd = [ffmpeg_path, '-y', '-i', filepath, '-ss', '00:00:00.5', '-vframes', '1', '-f', 'image2', temp_thumb_path]
            subprocess.run(thumb_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.rename(temp_thumb_path, thumb_path)
            print("[RTSP-Record] Generated thumbnail for {}".format(mp4_filename))
        else:
            print("[RTSP-Record] ffmpeg not found, skipping thumbnail generation.")
            
        process_synced_videos()
    except Exception as e:
        print("[RTSP-Record] Error finalizing video recording:", e)

def start_local_video_recording(duration, still_filename):
    global record_file_path, record_video_writer, record_until_time, record_frames_written
    import cv2
    import datetime
    
    try:
        time_part = still_filename.replace("img_auto_", "").split(".")[0]
        parts = time_part.split("_")
        if len(parts) >= 2:
            time_part = parts[0] + "_" + parts[1]
        vid_filename = "vid_{}.mp4".format(time_part)
    except Exception:
        vid_filename = "vid_{}.mp4".format(datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        
    filepath = os.path.join(VIDEOS_DIR, vid_filename)
    
    with record_lock:
        if record_video_writer is not None:
            try: record_video_writer.release()
            except: pass
        
        with frame_lock:
            if latest_frame_raw is not None:
                height, width, _ = latest_frame_raw.shape
            else:
                width, height = 1280, 720
                
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        record_video_writer = cv2.VideoWriter(filepath, fourcc, 10.0, (width, height))
        record_file_path = filepath
        record_until_time = time.time() + duration + 2.0
        record_frames_written = 0
        
    print("[RTSP-Record] Started local recording to {} for {}s".format(filepath, duration + 2.0))

def rtsp_thread_loop():
    global latest_frame_jpeg, latest_frame_time, latest_frame_raw, last_spray_time
    global record_file_path, record_video_writer, record_frames_written
    
    import cv2
    import collections
    import numpy as np
    import time
    import shutil
    
    circular_buffer = collections.deque(maxlen=30)
    prev_gray = None
    last_motion_save_time = 0.0
    last_settings_load = 0.0
    
    settings = load_settings()
    rtsp_url = settings.get('rtsp_stream_url', 'rtsp://pi3:8554/live')
    enable_rtsp = settings.get('enable_rtsp', True)
    
    frame_interval = 0.1
    
    print("[RTSP] Background stream thread started. URL: {}".format(rtsp_url))
    
    while True:
        if not enable_rtsp:
            time.sleep(2.0)
            settings = load_settings()
            enable_rtsp = settings.get('enable_rtsp', True)
            rtsp_url = settings.get('rtsp_stream_url', 'rtsp://pi3:8554/live')
            continue
            
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            print("[RTSP] Failed to open stream at {}. Retrying in 5 seconds...".format(rtsp_url))
            time.sleep(5.0)
            settings = load_settings()
            enable_rtsp = settings.get('enable_rtsp', True)
            rtsp_url = settings.get('rtsp_stream_url', 'rtsp://pi3:8554/live')
            continue
            
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        prev_gray = None
        
        while enable_rtsp:
            for _ in range(3):
                cap.grab()
                
            ret, frame = cap.read()
            if not ret:
                print("[RTSP] Stream connection lost. Reconnecting...")
                break
                
            now_time = time.time()
            circular_buffer.append(frame.copy())
            
            ret_jpg, jpeg_buf = cv2.imencode('.jpg', frame)
            if ret_jpg:
                with frame_lock:
                    latest_frame_jpeg = jpeg_buf.tobytes()
                    latest_frame_time = now_time
                    latest_frame_raw = frame.copy()
            
            with record_lock:
                if record_video_writer is not None:
                    if record_frames_written == 0:
                        print("[RTSP-Record] Writing pre-record buffer of {} frames...".format(len(circular_buffer)))
                        for bf in list(circular_buffer)[:-1]:
                            record_video_writer.write(bf)
                            record_frames_written += 1
                    
                    record_video_writer.write(frame)
                    record_frames_written += 1
                    
                    if now_time >= record_until_time:
                        print("[RTSP-Record] Recording finished. Total frames written: {}".format(record_frames_written))
                        record_video_writer.release()
                        
                        final_path = record_file_path
                        threading.Thread(target=finalize_video_recording, args=(final_path,)).start()
                        
                        record_video_writer = None
                        record_file_path = None
                        record_frames_written = 0
            
            if now_time - last_settings_load > 10.0:
                settings = load_settings()
                enable_rtsp = settings.get('enable_rtsp', True)
                rtsp_url = settings.get('rtsp_stream_url', 'rtsp://pi3:8554/live')
                last_settings_load = now_time
            
            local_dt = get_eastern_time()
            is_active_hour = (6 <= local_dt.hour < 20)
            
            if is_active_hour and automation_enabled:
                small_frame = cv2.resize(frame, (320, 240))
                gray = cv2.cvtColor(small_frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.GaussianBlur(gray, (21, 21), 0)
                
                if prev_gray is None:
                    prev_gray = gray
                    continue
                    
                frame_diff = cv2.absdiff(prev_gray, gray)
                thresh = cv2.threshold(frame_diff, 25, 255, cv2.THRESH_BINARY)[1]
                thresh = cv2.dilate(thresh, None, iterations=2)
                
                non_zero = np.sum(thresh == 255)
                total_pixels = thresh.shape[0] * thresh.shape[1]
                motion_percent = (non_zero / total_pixels) * 100.0
                
                prev_gray = gray
                
                if motion_percent > 0.8:
                    temp_filepath = os.path.join(RAW_DIR, 'temp_rtsp_inference.jpg')
                    try:
                        cv2.imwrite(temp_filepath, frame)
                        is_squirrel, confidence = model_predict(temp_filepath)
                        
                        threshold = float(settings.get('confidence_threshold', 0.70))
                        cooldown = float(settings.get('spray_cooldown_seconds', 60))
                        cooldown_active = (now_time - last_spray_time < cooldown)
                        meets_threshold = (confidence >= threshold)
                        decision = get_spray_decision(is_squirrel, confidence, settings, now_time)
                        
                        if is_squirrel and meets_threshold:
                            if decision['ready'] and not cooldown_active:
                                print("[RTSP-Inference] Squirrel confirmed! Conf: {:.1f}%. Spraying!".format(confidence*100))
                                duration = get_current_spray_duration()
                                last_spray_time = now_time
                                log_blast('auto', confidence, active_model_name)
                                
                                trigger_solenoid_on_pi(duration)
                                
                                now_str = local_dt.strftime("%Y%m%d_%H%M%S_%f")
                                filename = "img_auto_{}.jpg".format(now_str)
                                squirrel_path = os.path.join(SQUIRREL_DIR, filename)
                                shutil.copy(temp_filepath, squirrel_path)
                                
                                try:
                                    db_img = DBImage(
                                        filename=filename,
                                        category='squirrel',
                                        captured_at=local_dt,
                                        prediction_confidence=confidence,
                                        is_auto_classified=True
                                    )
                                    db_session.add(db_img)
                                    db_session.commit()
                                    log_message("[Auto-Classify] Saved auto image {} (conf: {:.2f})".format(filename, confidence))
                                except Exception as dbe:
                                    db_session.rollback()
                                    print("DB save failed:", dbe)
                                    
                                start_local_video_recording(duration, filename)
                            elif not decision['ready']:
                                print("[RTSP-Inference] Squirrel detection held for confirmation: {0}/{1} hits.".format(
                                    decision['hits'], decision['required_hits']
                                ))
                            else:
                                print("[RTSP-Inference] Squirrel found, but skipping spray because cooldown is active.")
                        else:
                            motion_interval = int(settings.get('rtsp_motion_interval_minutes', 5))
                            if now_time - last_motion_save_time > motion_interval * 60:
                                now_str = local_dt.strftime("%Y%m%d_%H%M%S_%f")
                                filename = "img_auto_{}.jpg".format(now_str)
                                raw_path = os.path.join(RAW_DIR, filename)
                                shutil.copy(temp_filepath, raw_path)
                                
                                try:
                                    db_img = DBImage(
                                        filename=filename,
                                        category='raw',
                                        captured_at=local_dt,
                                        prediction_confidence=confidence,
                                        is_auto_classified=False
                                    )
                                    db_session.add(db_img)
                                    db_session.commit()
                                    last_motion_save_time = now_time
                                    log_message("[RTSP-Motion] Saved candidate {} to raw queue (conf: {:.2f})".format(filename, confidence))
                                except Exception as dbe:
                                    db_session.rollback()
                                    print("DB save failed:", dbe)
                                    
                        if os.path.exists(temp_filepath):
                            os.remove(temp_filepath)
                    except Exception as ie:
                        print("Error in RTSP inference:", ie)
                        if os.path.exists(temp_filepath):
                            try: os.remove(temp_filepath)
                            except: pass
            
            time.sleep(frame_interval)
            
        cap.release()


background_services_started = False

def start_background_services():
    global background_services_started
    if background_services_started:
        return
    if setting_enabled(os.environ.get('DISABLE_BACKGROUND_SERVICES', False)):
        log_message("Background services disabled by DISABLE_BACKGROUND_SERVICES.")
        return

    background_services_started = True

    cleanup_thread = threading.Thread(target=run_storage_cleanup)
    cleanup_thread.daemon = True
    cleanup_thread.start()

    if load_settings().get('enable_rtsp', False):
        rtsp_thread = threading.Thread(target=rtsp_thread_loop)
        rtsp_thread.daemon = True
        rtsp_thread.start()
    else:
        log_message("Streaming backend disabled; RTSP/MJPEG reader thread not started.")


start_background_services()


if __name__ == '__main__':
    log_message("Starting Squirrel Soaker 9001 Classifier App...")
    log_message("Serving locally at http://localhost:5001")
    app.run(host='0.0.0.0', port=5001, debug=True, use_reloader=False)
