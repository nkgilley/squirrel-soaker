# Security Policy

## Supported versions

Only the latest `main` branch and the most recent tagged release receive fixes.

## Reporting a vulnerability

Please do not open a public issue for a security vulnerability. Contact the
repository maintainers privately through the security contact configured on
GitHub and include reproduction steps, affected versions, and a proposed
severity. Remove passwords, tokens, camera URLs, and public tunnel addresses
from reports unless they are essential to reproduction.

The application is intended for a trusted LAN or an authenticated private
tunnel. It should not be exposed directly to the public internet. Keep device
tokens enabled, restrict the reverse proxy to intended users, and rotate
credentials if they appear in logs or commits.
