# Troubleshooting

## The web UI is unavailable

Check `docker compose ps`, `docker compose logs --tail=100 squirrel-soaker`,
and `curl http://localhost:5001/api/health`. A reverse proxy or tunnel should
point to the host's published app port, not the container's internal network
address.

## The Pi is offline

On the Pi, inspect `systemctl status squirrel-capture squirrel-trigger`, then
read `journalctl -u squirrel-capture -u squirrel-trigger -n 100`. Confirm the
server hostname/IP and `DEVICE_API_TOKEN` in the Pi environment match the
server. Verify camera detection with `rpicam-hello --list-cameras`.

## Images are blurry or dark

Use Diagnostics and the image-quality health metrics. Check lens protection,
focus mode, case clearance, lighting, and the day/night camera settings. A
NoIR camera needs suitable IR illumination and may be worse in visible light.

## Videos are missing

Check Pi disk space and the event's media attachment in the UI. Then inspect
the trigger-server upload logs. The event should remain in history even when a
video upload failed or a video was deleted.
