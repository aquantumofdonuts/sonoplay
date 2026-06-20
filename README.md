# SonoPlay

Bridge Plexamp to DLNA/UPnP renderers on your local network.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](https://www.docker.com/)

## What It Does

Plexamp cannot natively cast to DLNA/UPnP targets. SonoPlay exposes your DLNA devices as Plex players so they appear in Plexamp's cast list.

Data path:

```text
Plexamp -> SonoPlay -> DLNA/UPnP speaker
```

## Key Features

- DLNA/UPnP device discovery
- Plex device registration via Plex.tv link flow
- Playback command translation (play, pause, seek, queue navigation)
- Virtual device groups for multi-room playback
- Web UI for linking and device management
- Docker-first deployment

## Quick Start

### Docker (recommended)

```bash
docker run -d \
  --name sonoplay \
  --network host \
  --restart unless-stopped \
  -v /path/to/config:/config \
  ghcr.io/aquantumofdonuts/sonoplay:latest
```

### Docker Compose

```bash
git clone --branch stable https://github.com/aquantumofdonuts/sonoplay.git
cd sonoplay
docker compose up -d
```

### First Use

1. Open http://your-server:32488
2. Link one or more devices to Plex
3. In Plexamp, open cast targets and select the linked device

## Configuration

Most installs work with defaults. To customize:

```bash
cp .env.example .env
```

Common settings:

- HTTP_PORT: Web UI/API port (default 32488)
- HOST_IP: Explicit host IP (auto-detected when unset)
- CLIENT_PROFILE: Plex transcoding profile

Note: docker-compose.yaml uses env_file.required and needs Docker Compose v2.17+.

## Requirements

- Docker (preferred) or Python 3.11+
- Plex Media Server on the same network
- Plex Pass for Plexamp
- DLNA/UPnP MediaRenderer-compatible targets

## Supported Devices

Any standards-compliant DLNA/UPnP renderer, including common Sonos, Yamaha, Denon/Marantz, and smart TV implementations.

## Project Docs

- API: docs/API.md
- Architecture: docs/ARCHITECTURE.md
- Detailed docs: docs/README-detailed.md
- Release notes: docs/release_notes/

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Run tests:

```bash
python3 -m pytest tests/ -q
```

## Contributing

Issues and PRs are welcome.

## Credits

Originally forked from plexdlnaplayer by songchenwen.

## License

GPL-3.0-or-later. See LICENSE.

