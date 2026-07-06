# Security Policy

## Supported versions

Only the latest release on PyPI receives security fixes.

## Reporting a vulnerability

Email **bernacas@gmail.com** (subject: `megabrain security`) or use GitHub's
private vulnerability reporting on this repo. Please don't open public issues
for security reports. You'll get an acknowledgment within a few days.

## Deployment notes

- `serve-api` binds `127.0.0.1` by default. If you bind further, set
  `--token` / `MEGABRAIN_API_TOKEN` — without it every endpoint (including
  `POST /index`) is open to the network.
- The engine sends chunk text to the configured embedding/chat endpoints
  (OpenRouter by default). For fully local operation point
  `MEGABRAIN_EMBED_BASE_URL` / `MEGABRAIN_CHAT_BASE_URL` at a local
  OpenAI-compatible server — nothing leaves the machine.
