# Changelog

All notable changes to this project will be documented in this file.

The latest version is always available by pulling the `latest` tag.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.2.5] - 2026-11-21

### Fixed

- Fixed first-track restarts and intermittent stops between tracks by confirming the renderer is playing the requested track. ([#24](https://github.com/aquantumofdonuts/sonoplay/pull/24), [#32](https://github.com/aquantumofdonuts/sonoplay/pull/32))
- Serialized volume commands and kept the latest requested value so slider drags do not overwhelm the renderer or cause the slider to jump backward. ([#26](https://github.com/aquantumofdonuts/sonoplay/pull/26))
- Stopped recreating adapters for offline renderers, so one disconnected device no longer interrupts playback on others. ([#29](https://github.com/aquantumofdonuts/sonoplay/pull/29))
- Preserved literal ampersands in XML CDATA and comments while repairing malformed SOAP replies. ([#33](https://github.com/aquantumofdonuts/sonoplay/pull/33))

### Logging

- Added the `LOG_LEVEL` setting and redacted Plex tokens in application and access logs. Reduced repeated DLNA messages and routine access logs while retaining failed HTTP requests. ([#27](https://github.com/aquantumofdonuts/sonoplay/pull/27), [#30](https://github.com/aquantumofdonuts/sonoplay/pull/30), [#33](https://github.com/aquantumofdonuts/sonoplay/pull/33))

Thanks to [@kcgthb](https://github.com/kcgthb) for the reports, fixes, and regression tests.

---

## [v1.2.4] - 2026-11-18

### Fixed

- SOAP control requests hanging and timing out on KEF LSX, Yamaha and other renderers that require a charset in `Content-Type` ([#13](https://github.com/aquantumofdonuts/sonoplay/issues/13), [#21](https://github.com/aquantumofdonuts/sonoplay/pull/21)). SonoPlay now sends `Content-Type: text/xml; charset="utf-8"` as the UPnP spec requires, instead of a non-standard `charset` header. Thanks @chowchinho and @chaun14.
- Yamaha AVR responses and event notifications with unescaped `&` failing to parse, which made SonoPlay skip through the whole queue at high request rates ([#15](https://github.com/aquantumofdonuts/sonoplay/issues/15), [#16](https://github.com/aquantumofdonuts/sonoplay/pull/16)). Bare ampersands are now escaped before parsing. Thanks @kcgthb.
- DSD files (`.dsf`, `.dff`) announced as `audio/mpeg` instead of `audio/dsd`, so strict renderers refused them ([#19](https://github.com/aquantumofdonuts/sonoplay/issues/19), [#20](https://github.com/aquantumofdonuts/sonoplay/pull/20)). Thanks @kcgthb.
- "Disabled" bitrate/sample-rate transcoding thresholds never being saved from the audio settings dialog ([#17](https://github.com/aquantumofdonuts/sonoplay/issues/17), [#18](https://github.com/aquantumofdonuts/sonoplay/pull/18)). Thanks @kcgthb.

### Documentation

- Documented `PMS_ADDRESS` for firewalls that block `*.plex.direct` DNS and for older renderers that reject HTTPS stream URLs.
- Removed the stale `git clone --branch stable` instruction from the README and website.
- `docker-compose.yaml` now references the published GHCR image, so `docker compose pull` upgrades in place.

---

## [v1.2.3] - 2026-07-20

### Fixed

- Samsung (and other strict UPnP renderer) soundbars/TVs rejecting `SetAVTransportURI` with a `UPnPError` SOAP Fault, causing casting to fail entirely while the device still appeared as a valid Plexamp target ([#10](https://github.com/aquantumofdonuts/sonoplay/issues/10)). Sonoplay now sends real DIDL-Lite `CurrentURIMetaData` (title/artist/album plus a `res` element with the correct MIME type) instead of an empty string, which is what strict renderers need to accept the resource.
- SOAP Fault parsing silently discarded the actual UPnP `errorCode`/`errorDescription` behind every `SetAVTransportURI`/`Stop`/etc. rejection (regardless of device brand), logging only a generic `"SOAP Fault: UPnPError"` string. A missing XML namespace mapping is now included, so logs surface the real error code and description for future diagnosis.

---

## [v1.2.2] - 2026-07-19

### Fixed

- Added `PMS_ADDRESS` env var to bypass `.plex.direct` DNS when a firewall change breaks Plex's public DNS resolution for local HTTPS connections. When set, all Plex Media Server connections (play queue fetch, stream URLs delivered to DLNA speakers) use plain HTTP to the specified LAN IP, restoring casting without requiring hairpin NAT.

---

## [v1.2.1] - 2026-06-20

Stability-focused release from the dev branch, centered on playback continuity, transport reliability, and runtime lifecycle hardening.

See [full release notes](docs/release_notes/v1.2.1) for details.

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
- Version metadata updated to 1.2.1 in runtime and package manifests

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

[v1.2.5]: https://github.com/aquantumofdonuts/sonoplay/compare/v1.2.4...v1.2.5
[v1.2.4]: https://github.com/aquantumofdonuts/sonoplay/compare/v1.2.3...v1.2.4
[v1.2.3]: https://github.com/aquantumofdonuts/sonoplay/compare/v1.2.2...v1.2.3
[v1.2.2]: https://github.com/aquantumofdonuts/sonoplay/compare/v1.2.1...v1.2.2
[v1.2.1]: https://github.com/aquantumofdonuts/sonoplay/compare/v1.1.0...v1.2.1
[v1.1.0]: https://github.com/aquantumofdonuts/sonoplay/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/songchenwen/plexdlnaplayer/tree/578399ead0fe606f562a897640a3c522226bdc22
