# Contributing

Thanks for helping improve Squirrel Soaker. This project controls physical
hardware, so changes that can trigger a spray require extra care.

## Development

1. Create a virtual environment and install `requirements.txt` and
   `requirements-dev.txt`.
2. Run `python -m pytest`, `ruff check .`, and
   `python -m compileall -q .` before opening a pull request.
3. Keep hardware access behind the existing controller boundaries and add a
   unit test for safety, settings, or persistence behavior.
4. Never commit `.env`, camera credentials, private network addresses, or
   personal media. Use `.env.example` and sanitized fixtures.

## Pull requests

Describe the user-visible behavior, hardware implications, migration steps,
and test commands. UI changes should include a screenshot. Hardware changes
should include a wiring or deployment note and a dry-run verification path.

Do not test a new spray path against a live solenoid until the duration limit,
cooldown, and emergency-disable behavior have been verified.
