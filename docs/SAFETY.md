# Safety

This project controls a physical water sprayer. Treat automation as a system
that can fail and keep people, pets, electronics, and neighbors clear of the
spray path.

- Use a normally closed water valve and a driver with an appropriate fuse and
  flyback protection.
- Keep the maximum duration, cooldown, and daily spray budget conservative.
- Use confirmation mode while tuning a new model or camera.
- Keep the app behind an authenticated reverse proxy or on a trusted LAN;
  never expose the Pi trigger endpoint directly to the internet.
- Test the emergency-disable control after every deployment.
- Inspect tubing, fittings, and the relay enclosure regularly for leaks or
  overheating.
