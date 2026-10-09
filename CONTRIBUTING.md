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

## Releases

Versions follow [Semantic Versioning](https://semver.org/). The stable Python API and the
pre-1.0 rule (breaking changes bump the minor version) are described in
[docs/api.md](docs/api.md). Every change that users notice gets a line under
*Unreleased* in [CHANGELOG.md](CHANGELOG.md), in the same pull request.

To publish a release (maintainers):

1. In `CHANGELOG.md`, rename *Unreleased* to `## X.Y.Z (YYYY-MM-DD)` and add a new, empty
   *Unreleased* section above it. List breaking changes to the stable API under *Breaking*.
2. Set the version in `pyproject.toml` and `src/smisim/__init__.py` to `X.Y.Z`.
3. Check locally: `python scripts/release_check.py vX.Y.Z` prints the release notes, or
   says what does not match.
4. Commit (`Release X.Y.Z`), push, wait for CI, then tag and push the tag:
   `git tag vX.Y.Z && git push origin vX.Y.Z`.

The *Release* workflow (`.github/workflows/release.yml`) then runs the checks and tests,
builds the sdist and wheel, uploads them to [PyPI](https://pypi.org/project/smi-bus-simulator/)
and publishes a GitHub release with the same files and the changelog section as notes.
Tags with a suffix (`v0.4.0-rc1`) become pre-releases; pip only installs those when asked
(`pip install --pre`).

PyPI upload uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/): there is
no API token in the repository. PyPI accepts uploads only from the `release.yml` workflow of
this repository running in the GitHub environment `pypi`. If you rename the workflow file or
the environment, change the trusted publisher on PyPI too. A version number can be uploaded
to PyPI only once, so a broken release is fixed with a new patch version, not by moving
the tag.

## Pull requests

1. Fork the repository and create a branch.
2. Keep the PR focused. Add or adjust tests.
3. Make sure `pytest` and `ruff` pass. CI runs both, plus a Docker build.
4. Describe what changed and why. For protocol changes, cite the source.

By contributing you agree that your contribution is licensed under the Apache License 2.0.
