# Changelog

All notable changes, one section per release, newest first. Versions follow
[Semantic Versioning](https://semver.org/); the stable Python API is listed in
[docs/api.md](docs/api.md). New entries go under *Unreleased* and move to a
`## X.Y.Z (YYYY-MM-DD)` section when the release is tagged (see CONTRIBUTING.md).

## Unreleased

### Added

* Stable in-process Python API: `smisim.Bus`, `BusSettings`, `FrameResult`, `Motor`,
  `MotorConfig`, `BlindKind`, `FAULTS` and the `smisim.protocol` names listed in
  docs/api.md. It works without the web server and without asyncio, and follows semver from
  0.3.0 on. `Bus.handle_frame()` now returns a `FrameResult` named tuple (still unpacks as
  `reply, delay`), `source` defaults to `"api"`, and `Bus()` can be built from settings alone.
* Test control on `Motor`: read-only `target_position`, `move_to(position)` (start a move
  without a telegram) and `set_position(position, *, tilt=None)` (stop and place the drive).
* Venetian option `tilt_in_position` (off by default, provisional): the reported position
  counts slat turning as drive-shaft rotation, so angle steps move it. New read-only
  `Motor.reported_position`; the Drive tab shows the value reported over SMI.
* Release workflow: pushing a `v*` tag checks that the tag, `pyproject.toml`,
  `smisim.__version__` and CHANGELOG agree, runs lint and tests, builds the sdist and
  wheel, and publishes a GitHub release with the changelog section as notes
  (`scripts/release_check.py`).

### Changed

* `time_scale` is checked the same way everywhere (0.1 to 50): CLI, config file, HTTP API.
  `smisim run --time-scale` now also overrides the value from `--config` (it was ignored).

### Documentation

* `time_scale` (CLI, config file, HTTP API, web UI) in README and docs/configuration.md.
* docs/configuration.md lists a public source or "assumption" for every default timing
  value (travel time, shaft rotation, upward factor, reversal pause, slat range, thermal
  limit, cooldown, answer delay, slow fault). The default travel times and shaft degrees
  imply faster shafts than published drive speeds; this is documented with a formula to
  adjust them. A test keeps the table in sync with the code.
* Releases: README no longer names a stale version; CHANGELOG keeps one section per
  release; CONTRIBUTING describes the release steps.

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
