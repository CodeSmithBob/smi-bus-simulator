"""Release hygiene: one version everywhere, a changelog section per release, a release job."""

import importlib.util
import re
import tomllib
from pathlib import Path

import pytest
import yaml

import smisim

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_check", ROOT / "scripts" / "release_check.py"
)
release_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_check)


def test_versions_agree():
    with open(ROOT / "pyproject.toml", "rb") as fh:
        assert tomllib.load(fh)["project"]["version"] == smisim.__version__


def test_changelog_has_one_section_per_release_newest_first():
    sections = release_check.changelog_sections((ROOT / "CHANGELOG.md").read_text())
    titles = [title for title, _ in sections]
    releases = titles[1:] if titles and titles[0] == "Unreleased" else titles
    assert "Unreleased" not in releases
    versions = []
    for title in releases:
        m = release_check.RELEASE_TITLE.match(title)
        assert m, f"changelog heading {title!r} is not 'X.Y.Z (YYYY-MM-DD)'"
        versions.append(tuple(int(p) for p in m["version"].split("-")[0].split(".")))
    assert versions == sorted(versions, reverse=True)
    assert len(set(versions)) == len(versions)
    assert (*map(int, smisim.__version__.split(".")),) in versions  # current version released


def test_release_check_accepts_the_current_version_and_rejects_others():
    notes = release_check.check(f"v{smisim.__version__}")
    assert notes.strip() and "##" not in notes.splitlines()[0]
    with pytest.raises(release_check.ReleaseError, match="does not match"):
        release_check.check("v99.0.0")
    with pytest.raises(release_check.ReleaseError, match="look like"):
        release_check.check(smisim.__version__)


def test_release_notes_need_a_non_empty_section():
    text = (
        "# Changelog\n\n## Unreleased\n\n* x\n\n"
        "## 1.2.0 (2026-01-01)\n\n## 1.1.0 (2025-12-01)\n\n* y\n"
    )
    assert release_check.release_notes(text, "1.1.0") == "* y\n"
    with pytest.raises(release_check.ReleaseError, match="empty"):
        release_check.release_notes(text, "1.2.0")
    with pytest.raises(release_check.ReleaseError, match="no '## 1.3.0"):
        release_check.release_notes(text, "1.3.0")


def test_release_workflow_runs_on_version_tags_and_publishes():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())
    triggers = wf[True] if True in wf else wf["on"]  # YAML 1.1 reads "on" as True
    assert triggers["push"]["tags"] == ["v*"]
    jobs = wf["jobs"]

    def runs(job):
        return " ".join(str(s.get("run", "")) for s in jobs[job]["steps"])

    build = runs("build")
    assert "scripts/release_check.py" in build and "pytest" in build
    assert "python -m build" in build and "twine check" in build

    # PyPI Trusted Publishing: must match the publisher registered on pypi.org
    # (workflow release.yml, environment "pypi").
    pypi = jobs["pypi"]
    assert pypi["needs"] == "build"
    assert pypi["environment"]["name"] == "pypi"
    assert pypi["permissions"] == {"id-token": "write"}
    assert any(
        str(s.get("uses", "")).startswith("pypa/gh-action-pypi-publish") for s in pypi["steps"]
    )
    # Only the publish job gets an OIDC token.
    assert "id-token" not in wf.get("permissions", {})

    assert jobs["github-release"]["needs"] == "pypi"
    assert jobs["github-release"]["permissions"]["contents"] == "write"
    assert "gh release create" in runs("github-release")


def test_readme_does_not_pin_an_old_version():
    readme = (ROOT / "README.md").read_text()
    stale = re.findall(r"release \(?v?(\d+\.\d+(?:\.\d+)?)\)?", readme)
    assert all(v == smisim.__version__ for v in stale), stale


def test_readme_links_work_on_pypi():
    # PyPI shows README.md as the project page; relative links and images break there.
    text = (ROOT / "README.md").read_text()
    targets = re.findall(r"\]\(([^)]+)\)", text)
    assert targets
    relative = [t for t in targets if not t.startswith(("https://", "http://", "#"))]
    assert relative == []
    assert "pip install smi-bus-simulator" in text
