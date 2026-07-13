# Releasing

1. Run the full CI commands from `CONTRIBUTING.md` and build the Docker image.
2. Review `git diff --stat` and scan tracked files for private IPs, usernames,
   credentials, camera URLs, and personal media.
3. Confirm no model weights, datasets, camera credentials, private IPs,
   personal paths, or personal media are tracked. Model files are intentionally
   excluded; each installation trains its own model.
4. Update `CHANGELOG.md`, tag the release, and publish the container image
   from the tag. Include supported Python and Raspberry Pi OS versions.
5. Verify a clean install using `.env.example`, then test the emergency
   disable path before enabling automation.
