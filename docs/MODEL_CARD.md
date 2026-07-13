# Model Card

## Intended use

The classifier estimates whether a camera frame contains a squirrel near the
configured feeder. It is a decision-support component for a low-power garden
repeller, not a general wildlife identifier.

## Limitations

Performance depends on crop, camera focus, lighting, weather, feeder layout,
and the balance of squirrel and non-squirrel examples. Day and night models
should be evaluated separately. False positives can cause unnecessary sprays;
use confirmation mode and conservative thresholds when changing models.

## Training and release provenance

Record the dataset period, class counts, grouped validation split, metrics,
source checkpoint, and SHA-256 checksum for every locally trained candidate
model. Model weights are deliberately excluded from this repository: every
installation must train its own model using its own camera and environment.
Keep private images and hard-negative media out of public releases.
