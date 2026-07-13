# Architecture

Squirrel Soaker has two cooperating processes:

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

The Pi is responsible for capture, local actuation, and short-lived video
buffers. The server owns inference, durable event history, training data, and
the web UI. Normal analysis frames stay in memory on the Pi and are sent to
the server; the Pi backlog is bounded so a disconnected server cannot fill
the SD card.

## Safety boundaries

- Pi commands require the configured device token.
- Spray duration, cooldown, daily budget, and confirmation mode are enforced
  before actuation.
- The trigger server performs local validation and has a manual emergency
  disable path.
- The IR plug is disabled by default and only changes state when its setting
  is enabled and the configured day/night period changes.

## Data flow and recovery

Events are separate from media attachments. Deleting a video does not erase
the event, classification history, or graph point. Failed uploads are retried
from the Pi's bounded backlog; the server remains the durable home for media.
See `docs/SAFETY.md` before connecting a new relay or solenoid.
