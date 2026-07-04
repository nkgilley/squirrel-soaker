#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
from io import BytesIO


CAMERA_LOCK_FILE = "/tmp/squirrel_soaker_camera.lock"


def find_camera_still_command():
    for binary in ("rpicam-still", "libcamera-still", "raspistill"):
        if shutil.which(binary):
            return binary
    return "raspistill"


def timed_ms(fn):
    started = time.time()
    result = fn()
    return result, round((time.time() - started) * 1000.0, 1)


def capture_jpeg(width, height, quality, rotation, roi):
    camera_cmd = find_camera_still_command()
    if camera_cmd in ("rpicam-still", "libcamera-still"):
        cmd = [
            camera_cmd,
            "--width", str(width),
            "--height", str(height),
            "--quality", str(quality),
            "--output", "-",
            "--timeout", "1000",
            "--nopreview",
            "--immediate",
            "--encoding", "jpg",
        ]
        if rotation in (0, 180):
            cmd.extend(["--rotation", str(rotation)])
        if roi:
            cmd.extend(["--roi", roi])
    else:
        cmd = [
            camera_cmd,
            "-w", str(width),
            "-h", str(height),
            "-q", str(quality),
            "-o", "-",
            "-t", "1000",
        ]
        if rotation in (90, 180, 270):
            cmd.extend(["-rot", str(rotation)])
        if roi:
            cmd.extend(["-roi", roi])
    lock_file = open(CAMERA_LOCK_FILE, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout_data, stderr_data = proc.communicate(timeout=20)
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        finally:
            lock_file.close()
    if proc.returncode != 0:
        raise RuntimeError(stderr_data.decode("utf-8", errors="ignore")[-1000:])
    return stdout_data


def pil_resize(jpeg_bytes, size):
    from PIL import Image

    img = Image.open(BytesIO(jpeg_bytes)).convert("RGB")
    img = img.resize(size, Image.LANCZOS)
    return img.size


def torch_probe(model_path, jpeg_bytes):
    result = {
        "torch_available": False,
        "model_path": model_path,
        "model_exists": bool(model_path and os.path.exists(model_path)),
        "inference_ms": None,
        "message": "",
    }
    try:
        import torch  # noqa: F401
    except Exception as exc:
        result["message"] = "torch import failed: {0}".format(exc)
        return result

    result["torch_available"] = True
    if not result["model_exists"]:
        result["message"] = "No local model file found; capture preprocessing only."
        return result

    result["message"] = "Torch is available; full Pi inference runner is not enabled by default yet."
    return result


def summarize(values):
    if not values:
        return None
    return {
        "min": round(min(values), 1),
        "median": round(statistics.median(values), 1),
        "max": round(max(values), 1),
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark Pi camera capture and local preprocessing.")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--width", type=int, default=960)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--quality", type=int, default=65)
    parser.add_argument("--rotation", type=int, default=int(os.environ.get("CAMERA_ROTATION", "0")))
    parser.add_argument("--roi", default=os.environ.get("CAMERA_ROI", ""))
    parser.add_argument("--model", default=os.environ.get("PI_MODEL_PATH", os.path.expanduser("~/squirrel_soaker/model.pth")))
    args = parser.parse_args()

    iterations = max(1, min(args.iterations, 10))
    capture_times = []
    resize_times = []
    byte_sizes = []
    last_jpeg = b""

    with tempfile.TemporaryDirectory(dir="/dev/shm" if os.path.isdir("/dev/shm") else None):
        for _idx in range(iterations):
            last_jpeg, capture_ms = timed_ms(
                lambda: capture_jpeg(args.width, args.height, args.quality, args.rotation, args.roi)
            )
            _size, resize_ms = timed_ms(lambda: pil_resize(last_jpeg, (224, 224)))
            capture_times.append(capture_ms)
            resize_times.append(resize_ms)
            byte_sizes.append(len(last_jpeg))

    result = {
        "status": "success",
        "iterations": iterations,
        "camera_command": find_camera_still_command(),
        "capture_ms": summarize(capture_times),
        "pil_resize_ms": summarize(resize_times),
        "jpeg_bytes": summarize(byte_sizes),
        "torch": torch_probe(args.model, last_jpeg),
    }

    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
