# Changelog

## Unreleased

### Added

* Stable in-process Python API: `smisim.Bus`, `BusSettings`, `FrameResult`, `Motor`,
  `MotorConfig`, `BlindKind`, `FAULTS` and the `smisim.protocol` names listed in
  docs/api.md. It works without the web server and without asyncio, and follows semver from
  0.3.0 on. `Bus.handle_frame()` now returns a `FrameResult` named tuple (still unpacks as
  `reply, delay`), `source` defaults to `"api"`, and `Bus()` can be built from settings alone.

## 0.2.0 (2026-10-09)

* Two views per window: front view (lift) and side section (slat angle, roll diameter,
  awning reach, daylight in the room). Switch with *View → Front + section*. The Drive tab
  shows both views large.
* Slat position in KNX convention (0 % open, 100 % closed) and a configurable physical
  slat angle range (`slat_min_deg`, `slat_max_deg`).
* Reversal dead time when a running drive changes direction (`reversal_pause_s`).
* Drive tab: open / half / close slat buttons, a slat % slider and "keep the slat
  angle after the move" (GOTO with position + angle).
* Daylight estimate per drive in the API (`daylight`) and the UI.

## 0.1.0 (2026-10-08)

First public release.

* SMI telegram codec: slave, manufacturer, broadcast, group-mask and key-ID addressing;
  drive commands with option, position and angle data; diagnosis; queries; checksum.
* Drive model for roller shutters, venetian blinds, screens and awnings: travel,
  slat tilt, angle steps, Pos1/Pos2, thermal protection, obstacle detection, calibration.
* Bus model with up to 16 drives per line, wired-AND answers and a traffic log.
* Transports: TCP, PTY (virtual serial port), physical serial port, shared real bus.
* Web UI: animated facade, drive panel, fault injection, built-in master, bus monitor,
  test lab and protocol status table.
* Python master library with slave-ID discovery and addressing; `smisim` CLI.
* Docker image and compose file; GitHub Actions CI.
