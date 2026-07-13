# Architecture

Squirrel Soaker has two cooperating processes:

```mermaid
flowchart LR
  subgraph PI["Raspberry Pi 5"]
    C["Camera Module 3"] --> L["Capture loop"]
    L -->|"JPEG + telemetry"| A
    T["Trigger server"] --> G["GPIO driver"] --> S["Solenoid"]
    T --> V["Short-lived event video"]
  end
  subgraph SERVER["Mac / Docker host"]
    A["Flask API"] --> M["PyTorch inference"] --> Q["Decision gate + safety"]
    A --> I["Live image in memory"]
    Q -->|"authenticated command"| T
    A --> D[("SQLite + media")]
    M --> D
    D --> U["Dashboard + training"]
  end
  V -->|"upload"| D
  SERVER -. "optional night power" .-> K["TP-Link/Kasa plug"]
  classDef device fill:#dbeafe,stroke:#2563eb,color:#172554
  classDef app fill:#dcfce7,stroke:#16a34a,color:#14532d
  classDef data fill:#fef3c7,stroke:#d97706,color:#78350f
  class C,L,T,G,S,V,K device
  class A,M,Q,I,U app
  class D data
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
