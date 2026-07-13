# Releasing

1. Run the full CI commands from `CONTRIBUTING.md` and build the Docker image.
2. Review `git diff --stat` and scan tracked files for private IPs, usernames,
   credentials, camera URLs, and personal media.
3. Generate a checksum for each model asset, for example:
   `shasum -a 256 model.pth > model.pth.sha256`.
4. Publish model weights as checksummed release assets or Git LFS objects.
   Do not add historical training snapshots, datasets, or personal videos to
   the public repository.
5. Update `CHANGELOG.md`, tag the release, and publish the container image
   from the tag. Include supported Python and Raspberry Pi OS versions.
6. Verify a clean install using `.env.example`, then test the emergency
   disable path before enabling automation.
