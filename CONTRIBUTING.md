# Contributing

Thanks for helping. Bug reports, protocol corrections, new drive models, UI work and
documentation are all welcome.

## Development setup

```bash
git clone https://github.com/codesmithbob/smi-bus-simulator
cd smi-bus-simulator
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                 # unit + end-to-end tests
ruff check . && ruff format --check .
smisim run --pty /tmp/smi -v
```

The web UI is plain HTML, CSS and JavaScript in `src/smisim/web/static/`, with no build
step. Reload the browser after editing.

## Ground rules

* **Protocol changes need a source.** Every wire-level element has a status in
  `SPEC_STATUS` (`src/smisim/protocol/constants.py`): `verified`, `observed` or
  `provisional`. If you change or add one, give the source (vendor manual, a bus capture you
  made yourself, open-source code) in the PR, and update `docs/protocol.md`.
* **Do not copy the members-only SMI specification** or other copyrighted documents into
  the repository. Describe the facts in your own words. A capture you made yourself
  (hex bytes plus what the drive did) is the best evidence.
* **Keep provisional encodings central.** New codes go into the tables in
  `constants.py`, so one change fixes the codec, the simulator and the UI.
* **Tests.** Add a test for each protocol rule. Published captures go into
  `tests/test_protocol.py` with a comment naming the source.
* **No vendor impersonation.** Use generic names for simulated drives. Manufacturer codes
  are configurable, and defaults stay neutral.

## Where things live

| Area | File |
|---|---|
| Telegram layout, codes, statuses | `src/smisim/protocol/constants.py` |
| Encode / decode / answers | `src/smisim/protocol/frames.py` |
| Byte stream to telegrams | `src/smisim/protocol/assembler.py` |
| Drive behaviour | `src/smisim/motor.py` |
| Line behaviour, wired-AND, traffic log | `src/smisim/bus.py` |
| Transports | `src/smisim/transports/` |
| Test lab checks | `src/smisim/testlab.py` |
| HTTP API | `src/smisim/web/server.py` |
| Facade drawing | `src/smisim/web/static/facade.js` |
| UI logic | `src/smisim/web/static/app.js` |

## Ideas for contributions

* SMI 3.0 D14 properties and parameter read/write (needs sources)
* More drive types (vertical blinds, skylight shutters, ZIP screens with wind alarm)
* Modbus/KNX/MQTT bridges for the simulated lines
* Recording and replaying bus traces (`.csv` import)
* A sun-position view that shows which facade gets direct sun
* Translations of the UI

## Pull requests

1. Fork the repository and create a branch.
2. Keep the PR focused. Add or adjust tests.
3. Make sure `pytest` and `ruff` pass. CI runs both, plus a Docker build.
4. Describe what changed and why. For protocol changes, cite the source.

By contributing you agree that your contribution is licensed under the Apache License 2.0.
