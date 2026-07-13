# Changelog

All notable changes are documented here.

## Unreleased

- Reorganized shared modules, Pi agents, utilities, documentation, and assets
  into dedicated directories; removed retired ESP32, Pi 3 stream, rsync, and
  scratch migration files.
- Added Phase 6 open-source project documentation and contribution templates.
- Removed personal LAN and home-directory defaults from the tracked deployment
  configuration; configure them through environment variables or deployment
  overrides.
- Added image quality telemetry and optional TP-Link/Kasa IR camera power
  control.

## 0.1.0

Initial reliability milestone. See the architecture and release documents in
`docs/` for the current system design and maintenance guidance.
