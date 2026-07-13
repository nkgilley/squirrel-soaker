# Building the Squirrel Soaker 9001: An AI-Powered, Water-Blasting Feeder Sentry

![Squirrel Soaker 9001](assets/header.png)

Have you ever filled a bird feeder with premium seed, only to watch a horde of squirrels perform acrobatics, consume the entire supply in minutes, and scare away the local birds? 

Frustrated by this endless battle, I decided to stop relying on "squirrel-proof" feeders and build a high-tech solution: **The Squirrel Soaker 9001**. 

This project combines a **Raspberry Pi** edge node, a **PyTorch ResNet-18** deep learning model running on a local server, a **GPIO-controlled solenoid water valve**, and a **modern stats dashboard web app** to build the ultimate automated birdfeeder sentry.

Here is the complete engineering journey of how it was built.

> **Current implementation:** The project now uses a Raspberry Pi 5 with a
> Camera Module 3 and local GPIO control for the solenoid and manual button.
> The Mac/Docker server performs inference and stores media. Model weights are
> intentionally excluded from the repository; each installation trains its
> own model from its own camera data. Optional day/night NoIR camera power is
> controlled through a TP-Link/Kasa plug.

---

## System Architecture Overview

The system is split into two nodes: the **Raspberry Pi Sentry** (mounted near the feeder) and the **Central Mac AI Server** (running locally in the house). 

```mermaid
flowchart TB
    subgraph CAPTURE["RASPBERRY PI 5 · CAPTURE"]
        direction LR
        SUN["Sunrise / sunset"] -.-> PLUG["Kasa smart plug"] -.-> NIGHT["NoIR camera"]
        DAY["Day camera"] --> CAP["RAM-first capture loop"]
        NIGHT --> CAP
    end

    subgraph INTELLIGENCE["MAC / DOCKER · AI + SAFETY"]
        direction LR
        API["Frame API"] --> MODEL["Day / night inference"]
        MODEL --> DETECT{"Enough confidence<br/>and repeat hits?"}
        DETECT --> SAFE{"Mode, cooldown<br/>and budget allow spray?"}
    end

    subgraph ACTION["RASPBERRY PI 5 · ACTUATION"]
        direction LR
        BUTTON["Manual button"] --> TRIGGER["Trigger server<br/>local safety limits"]
        TRIGGER --> DRIVER["Protected GPIO driver"] --> VALVE["Water solenoid"]
        TRIGGER --> VIDEO["Event video"]
    end

    subgraph HISTORY["SERVER · HISTORY + CONTROL"]
        direction LR
        EVENTS[("Events + decisions")] --> UI["Dashboard<br/>review · settings · training"]
        MEDIA[("Images + videos")] --> UI
    end

    CAP == "JPEG + health telemetry" ==> API
    SAFE == "authenticated spray command" ==> TRIGGER
    API --> LIVE["Live preview"] --> MEDIA
    MODEL --> EVENTS
    VIDEO == "upload" ==> MEDIA

    classDef pi fill:#e8f1ff,stroke:#3b82f6,color:#172554
    classDef server fill:#e9f8ef,stroke:#22a06b,color:#153c2b
    classDef safety fill:#fff4d6,stroke:#d89b18,color:#4a3500
    classDef data fill:#f3efff,stroke:#7c5ce0,color:#2b1b5a
    classDef hardware fill:#f1f3f5,stroke:#687076,color:#202428
    class DAY,NIGHT,CAP,TRIGGER,PLUG pi
    class API,MODEL,LIVE,SUN server
    class DETECT,SAFE safety
    class EVENTS,MEDIA,UI data
    class BUTTON,VIDEO,DRIVER,VALVE hardware
    style CAPTURE fill:transparent,stroke:#8aa7d6,stroke-width:1px
    style INTELLIGENCE fill:transparent,stroke:#79b69a,stroke-width:1px
    style ACTION fill:transparent,stroke:#8aa7d6,stroke-width:1px
    style HISTORY fill:transparent,stroke:#9b8ad6,stroke-width:1px
```

### The Hardware Setup
- **Raspberry Pi 5**: Acts as the edge controller and keeps normal frames in memory.
- **Pi Camera Module**: Positioned with a tight digital zoom (Region of Interest) focused on the feeder tray.
- **12V Solenoid Valve & Relay Module**: Plumbed into a garden hose and controlled through a properly rated Pi GPIO driver.

---

## The Four Phases of Development

### Phase 1: Edge Capture & Data Sync
Before building an AI model, I needed data. I wrote the first version of the Raspberry Pi capture daemon, now located at `pi/capture.py`, to collect still frames. The original build pulled those files with rsync; the current Pi 5 agent posts in-memory frames directly and keeps only a bounded failure backlog.

To sort this initial raw data into training folders (`squirrel` vs. `not_squirrel`), I built a custom **Flask Web Application** with an image review queue. I integrated keyboard shortcuts and a history-based **Undo Stack** so I could fly through hundreds of raw pictures and manually label them with high speed.

### Phase 2: Deep Learning with PyTorch
Once I collected about a thousand images, it was time to train the brain. 

I wrote `tools/train.py` using **PyTorch** to fine-tune a pre-trained **ResNet-18** convolutional neural network. The web UI now saves every successful training run as a timestamped checkpoint, then asks whether to make that new checkpoint the active model.

#### Addressing the Class Imbalance
My dataset had a classic class imbalance: **34 squirrel images** vs. **980 not-squirrel (birds/empty) images**. To prevent the model from simply guessing "not-squirrel" every time, I:
1. Implemented **Weighted Cross-Entropy Loss** to penalize misclassifications of the rare class more heavily.
2. Fine-tuned the *entire* network (rather than freezing the convolutional base) so the model could adapt its lower-level filters to the specific lighting, background, and zoom of my backyard bird feeder.

After 10 epochs, the model achieved **98.51% validation accuracy**, correctly identifying **91.2%** of the squirrels (with high confidence >70%) while maintaining near-zero false positives.

I integrated this model into the Mac Flask server at `/api/predict`. When the Pi captures a photo, it sends it here for inference. If the model is confident (>85%), it automatically moves the photo to the dataset folders, saving me manual sorting time!

### Phase 3: Containerization & Daemons
To make the system robust:
1. **Dockerized the Flask Server**: Created a lightweight `Dockerfile` using CPU-only PyTorch wheel downloads to keep the image footprint around 600MB. Configured a `docker-compose.yml` for easy hosting in Docker and Unraid.
2. **systemd Services on the Pi**: Created permanent service templates under `pi/systemd/` with automatic restart behavior so the sentry recovers after power or network interruptions.

---

## Phase 4: The Stats Dashboard

A high-tech sentry needs a command center. I updated the web app to feature a modern, dark-themed **Stats Dashboard** as the homepage, moving the manual classifier into a secondary view.

```
+--------------------------------------------------------------+
|  🐿️ Squirrel Soaker 9001                   [Automation: ON]  |
+--------------------------------------------------------------+
| View Mode:     |                                             |
| [Dashboard]    |  SYSTEM DASHBOARD 📊                        |
| [Classify]     |  +------------+ +------------+ +---------+  |
| [Videos]       |  | BLASTS: 12 | | LOOP: ACT  | | RAW: 2  |  |
|                |  +------------+ +------------+ +---------+  |
| Keyboard:      |                                             |
| space: spray   |  [ Blast Activity Graph ]  [ Live Feed ]    |
| z: undo        |  |   Auto vs. Manual    |  | Snapshot |    |
|                |  |   last 7 days        |  |  (15s)   |    |
+--------------------------------------------------------------+
```

### 📊 The 7-Day Activity Graph
Using **Chart.js**, the dashboard aggregates event data from a persistent `data/blasts_log.json` file. It displays a dual-bar chart representing water blasts over the last 7 days, color-coding auto-detections (green) and manual sprays (blue) side-by-side.

The system also builds daily compilation videos from spray recordings. Local
compilation videos are stored under `data/videos/` and are intentionally not
included in the public repository.

### 📹 Live Snapshot Feed (The lock-contention problem)
I wanted a live video feed on the dashboard, but hit an interesting engineering constraint: **device lock contention**. 

If I streamed continuous MJPEG/RTSP video from `/dev/video0` to the browser, the device handle would stay occupied. This blocked the background motion-detection script (`raspistill`) from grabbing frames, which disabled the AI.

**The Solution**: A "Live-ish" snapshot preview. I exposed a `/api/latest_image` route serving the newest JPEG across directories with cache-disabling headers. The dashboard polls this image every 15 seconds (matching the sync frequency) using a cache-busting timestamp (`?t=Date.now()`). 

To make it clear at a glance, I added a **dynamic status badge** inside the feed box:
- 🟢 `LIVE`: Green when active during daytime hours.
- 🟡 `SLEEPING (Night)`: Yellow when local time is outside configured shooting hours (8:00 PM to 6:00 AM), indicating the Pi camera loop is sleeping.
- 🔴 `IDLE / OFFLINE`: Red if it is daytime but no frames have synced for more than 5 minutes.

---

## Engineering Lessons Learned

1. **Beware of device locks on the edge**: Dedicated camera hardware like the Pi Camera module does not share access easily. Architecting around snapshot files rather than streams keeps edge daemons light and cooperative.
2. **Loss weighting is magic for small datasets**: When you only have 34 positive examples, standard neural nets will ignore them. Applying weights to the loss function forces the model to learn what a squirrel looks like.
3. **Detached training subprocesses**: Running PyTorch retraining inside Flask's main thread is a recipe for memory leaks and server crashes. Spawning it in a background process using the active interpreter and logging stdout to file keeps the UI snappy.

---

## Credits & Inspiration

This project was inspired by the original automated water blaster concept described in the blog post: *"How to build a Raspberry Pi-powered squirrel detector and water blaster"* (which set up the framework of motion sensing and solenoid relays). Building on top of that base with PyTorch training, Docker containers, and a full statistics dashboard took the concept to the next level.

The source code and configuration files are fully open-source and hosted on my GitHub!
