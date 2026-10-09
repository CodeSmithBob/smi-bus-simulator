"""docs/configuration.md lists a source (or "assumption") for every default timing value.

This test keeps that table and the code defaults in sync.
"""

import re
from dataclasses import fields
from pathlib import Path

from smisim import BusSettings, MotorConfig
from smisim.client import SmiMaster
from smisim.motor import KIND_DEFAULTS, SLOW_FAULT_DELAY_S, BlindKind

DOC = Path(__file__).resolve().parents[1] / "docs" / "configuration.md"
SECTION = "## Where the default timing values come from"
ROW = re.compile(
    r"^\| `(?P<name>[\w ]+?)`(?: (?P<extra>\(\w+\)|fault delay))? "
    r"\| `(?P<value>[\d.]+)(?: ms)?` \| (?P<source>.+) \|$"
)

REQUIRED = {
    "travel_time_s",
    "up_speed_factor",
    "reversal_pause_s",
    "response_delay_ms",
    "thermal_limit_s",
    "cooldown_s",
    "tilt_degrees",
    "shaft_degrees",
}


def table_rows() -> list[re.Match]:
    text = DOC.read_text()
    assert SECTION in text
    section = text.split(SECTION, 1)[1]
    rows = [ROW.match(line) for line in section.splitlines()]
    return [r for r in rows if r]


def default_of(name: str):
    for cls in (MotorConfig, BusSettings):
        for f in fields(cls):
            if f.name == name:
                return f.default
    raise KeyError(name)


def test_every_timing_default_is_documented_with_a_source():
    rows = table_rows()
    names = {r["name"] for r in rows}
    assert REQUIRED <= names
    for r in rows:
        source = r["source"]
        assert "**Assumption" in source or re.search(r"\[\d\]", source), r.group(0)


def test_documented_values_match_the_code():
    for r in table_rows():
        name, extra, value = r["name"], r["extra"], float(r["value"])
        if name == "slow":
            assert value / 1000 == SLOW_FAULT_DELAY_S
            assert SLOW_FAULT_DELAY_S > SmiMaster.__init__.__kwdefaults__["timeout"]
        elif extra:
            kind = BlindKind(extra.strip("()"))
            assert KIND_DEFAULTS[kind][name] == value, (name, kind)
        else:
            assert default_of(name) == value, name
    for kind in BlindKind:  # every kind's travel time and shaft degrees are listed
        listed = {(r["name"], r["extra"]) for r in table_rows()}
        assert ("travel_time_s", f"({kind.value})") in listed
        assert ("shaft_degrees", f"({kind.value})") in listed


def test_profile_table_matches_the_code():
    from smisim import DRIVE_PROFILES, MotorConfig

    section = DOC.read_text().split("## Drive mechanics and profiles", 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        if len(cells) == 7 and cells[0] in DRIVE_PROFILES:
            rows[cells[0]] = cells
            assert "**Assumption" in cells[6], line
    assert set(rows) == set(DRIVE_PROFILES)
    for name, cells in rows.items():
        cfg = MotorConfig.from_profile(name)
        assert cells[1] == cfg.kind.value
        assert float(cells[2]) == cfg.tilt_degrees
        assert [int(c) for c in cells[3:6]] == [cfg.slack, cfg.top_offset, cfg.bottom_offset]
