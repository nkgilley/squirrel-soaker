FROM python:3.14-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RUNNING_IN_DOCKER=true

# Set working directory
WORKDIR /app

# Install system dependencies (ffmpeg is required for wrapping video recordings)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch and Torchvision CPU-only to minimize container size
RUN pip3 install --no-cache-dir \
    torch==2.13.0+cpu torchvision==0.28.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu

# Install Flask and other application dependencies
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application files. Trained checkpoints live in the mounted data volume.
COPY classify_images.py auto_label.py train.py squirrel_safety.py squirrel_health.py squirrel_settings.py squirrel_kasa.py /app/

# Expose the Flask server port
EXPOSE 5001

# Run the Flask app using Gunicorn (production WSGI server)
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "1", "--threads", "12", "--timeout", "120", "classify_images:app"]
