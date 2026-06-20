# Changelog

All notable changes to this project will be documented in this file.

The latest version is always available by pulling the `latest` tag.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.2.0] - 2026-06-20

Stability-focused release from the dev branch, centered on playback continuity, transport reliability, and runtime lifecycle hardening.

See [full release notes](docs/release_notes/v1.2.0) for details.

### Fixed

- End-of-track playback stopping instead of advancing to the next queue item
- Stale Plex stop commands interrupting auto-next transitions
- Auto-next double-fire behavior and end-of-queue stop handling edge cases
- Event loop descriptor leak/exhaustion risks in background state loop teardown
- Potential lost updates during concurrent settings read-modify-write mutations

### Changed

- State polling now tolerates partial SOAP failures without dropping the full poll cycle
- Transport operations can be cancelled immediately on stop requests
- Play command timing now prefers capability/readiness checks over fixed delay sleeps
- Subscribe loop ownership tracking prevents old loops from resuming beside new loops
- Pending notify wait tasks are cancelled/drained between iterations to avoid buildup
- Stats persistence moved off the async hot path to reduce event-loop stalls

### Documentation

- README rewritten to be concise and operationally focused
- Version metadata updated to 1.2.0 in runtime and package manifests

---

## [v1.1.0] - 2026-03-07

A major release covering everything from the original fork through today: virtual device groups, a modern web UI, broad DLNA device compatibility, security hardening, accessibility, and stability improvements.

See [full release notes](docs/release_notes/v1.1.0) for details.

### Highlights

- **Virtual Device Groups** — combine multiple DLNA speakers for synchronized multi-room audio
- **Modern Web UI** — responsive dual-page design with real-time status, search, and playback controls
- **DLNA Compatibility** — device quirks system for Sony, Denon, Marantz, Bose; HTTP 5xx retry logic; XML sanitization for Oppo, Sonos, Belkin, and others
- **Security** — 14 CVEs resolved, XSS prevention, XML injection protection, pinned dependencies
- **Accessibility** — keyboard focus, reduced-motion, ARIA attributes, semantic elements
- **Stability** — proper shutdown cleanup, thread-safe data access, graceful device disconnect handling
- **Infrastructure** — multi-stage Dockerfile, non-root container user, healthcheck, GitHub Actions CI/CD

---

## [v1.0.0] - Original Baseline

Original implementation by [@songchenwen](https://github.com/songchenwen).

Based on commit `578399ead0fe606f562a897640a3c522226bdc22`.

### Features
- UPNP auto discovery to find DLNA devices in LAN
- Plex GDM to notify Plex clients about DLNA devices
- Connect DLNA devices to plex.tv for Plexamp support
- Basic web UI with HTML table for device management
- Device linking via plex.tv PIN authentication

---

[v1.2.0]: https://github.com/aquantumofdonuts/sonoplay/compare/v1.1.0...v1.2.0
[v1.1.0]: https://github.com/aquantumofdonuts/sonoplay/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/songchenwen/plexdlnaplayer/tree/578399ead0fe606f562a897640a3c522226bdc22
