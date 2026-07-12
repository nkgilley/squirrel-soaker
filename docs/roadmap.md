# Squirrel Soaker 9001 Improvement Roadmap

This roadmap was produced from a full review of the Mac server, Raspberry Pi
agent, model training workflow, web UI, deployment files, and repository
presentation on July 12, 2026.

Implementation status: the Phase 1/2 baseline is committed in `f3808e0`.
Phase 3 has started in `02139a1` with the health-history store extracted from
the server monolith.

## Review Summary

The current system is capable and already solves the complete capture,
classification, spray, media, and review workflow. Its main risks come from
physical-control safety, unauthenticated device APIs, blocking Pi operations,
shared global model state, weak model evaluation, and a lack of automated
tests. The repository also needs conventional packaging, CI, contributor
documentation, licensing, and smaller modules before it will feel like a
high-quality open-source project.

The recommended architecture is a modular server application plus a small Pi
agent. It should remain easy to self-host and should not be split into a fleet
of microservices.

```text
Camera/GPIO -> Pi agent -> authenticated API -> Server application
                  |                              |-- SQLite + media
                  |-- safety controller          |-- background worker
                  |-- retry/outbox               |-- model registry
                  `-- edge fallback              `-- web UI
```

## Phase 1: Safety And Reliability

- Enforce a hard maximum spray duration on the Pi, independent of UI settings.
- Add rolling spray-count and total-open-time safety budgets.
- Authenticate device traffic between the server and Pi.
- Require authentication for sensitive browser operations.
- Validate settings and command parameters on the server, not only in HTML.
- Limit image and video upload sizes.
- Require the matching confirmation ID before accepting a pending spray.
- Serialize every GPIO operation through one safety lock.
- Replace the Pi's single-threaded HTTP server with bounded concurrent request
  handling so diagnostics cannot block spray control.
- Add subprocess cancellation and recovery for camera tests and benchmarks.
- Lock model loading and prediction so day/night switching cannot race.
- Let a newly trained model be activated explicitly for day, night, or both.
- Make settings writes atomic and resilient to concurrent reads.
- Add Docker health checks and document always-awake host requirements.
- Move slow synchronization and media work out of request threads.

Exit criteria: a 24-hour soak test, responsive LAN and remote access, no
duplicate sprays, no valve activation beyond configured safety limits, and
successful recovery from an offline or wedged camera without restarting the
Mac application.

## Phase 2: Regression Foundation

- Add `pyproject.toml` and explicit development dependencies.
- Pin or lock production dependencies for repeatable builds.
- Add pytest tests that use temporary storage and fake GPIO/camera adapters.
- Test duration limits, safety budgets, authentication, spray gating,
  cooldowns, confirmation mode, model targeting, uploads, and Pi outages.
- Move hardware activation scripts outside automated test discovery.
- Add Ruff and a gradual type-checking baseline.
- Add GitHub Actions for syntax checks, unit tests, linting, and Docker builds.
- Add a non-destructive integration test profile.

Exit criteria: tests run on a development Mac and GitHub Actions without a Pi,
camera, production database, model checkpoint, or real solenoid attached.

## Phase 3: Modular Architecture

- Introduce a Flask application factory and blueprints.
- Split database, settings, inference, spray, media, training, health, and
  notification responsibilities into focused modules.
- Extract templates, CSS, and JavaScript from `classify_images.py`.
- Introduce typed settings and versioned API schemas.
- Store secrets only in environment variables or Docker secrets.
- Use Alembic migrations, SQLite WAL mode, foreign keys, and explicit
  filesystem/database reconciliation.
- Move training, FFmpeg, retention cleanup, and synchronization into a durable
  worker process with persisted job status.

## Phase 4: Model Quality

- Record event, camera, crop, day/night period, and model provenance for every
  training frame.
- Split train, validation, and test data by event or time block to prevent
  adjacent-frame leakage.
- Report precision, squirrel recall, false-positive rate, confusion matrices,
  and threshold curves rather than overall accuracy alone.
- Train and evaluate day and night models independently.
- Add model cards, checksums, champion/challenger evaluation, shadow mode, and
  one-click rollback.
- Track hard-negative provenance and prevent duplicate extracted frames.

## Phase 5: Product Features

- Detect blur, obstruction, bad exposure, and autofocus failures automatically.
- Add an event-detail view showing every frame and decision behind a spray.
- Add visible safety budgets and an emergency-disable control.
- Add optional optimized Pi inference for server-outage fallback while keeping
  durable media on the server.
- Add Web Push/PWA confirmation, backup/export/restore, setup guidance, and a
  guided hardware self-test.
- Add optional IR illumination scheduling and independent night calibration.

## Phase 6: Open-Source Release

- Choose a project license and audit model and dependency licensing.
- Add contributing, security, code-of-conduct, changelog, architecture, wiring,
  safety, troubleshooting, and release documentation.
- Add screenshots, demo data, issue templates, and a pull-request template.
- Remove personal IP addresses, usernames, paths, and location defaults.
- Publish model weights as checksummed release assets or through Git LFS.
- Remove historical model blobs before the first public release.
- Publish versioned container images, an SBOM, and signed release artifacts.

The first public milestone should be a `v0.1` reliability release containing
Phases 1 and 2. Later phases should preserve behavior through incremental
refactoring rather than a ground-up rewrite.
