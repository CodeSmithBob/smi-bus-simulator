"""Check that a release tag matches the code and print its changelog section.

Used by .github/workflows/release.yml and runnable by hand before tagging:

    python scripts/release_check.py v0.3.0 --notes release-notes.md

Checks that the tag is ``v<version>``, that pyproject.toml and smisim.__version__ both
carry that version, and that CHANGELOG.md has a non-empty ``## <version> (YYYY-MM-DD)``
section. Writes that section (without its heading) to ``--notes``.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADING = re.compile(r"^## (?P<title>.+?)\s*$")
RELEASE_TITLE = re.compile(
    r"^(?P<version>\d+\.\d+\.\d+(?:[-+][\w.]+)?) \((?P<date>\d{4}-\d{2}-\d{2})\)$"
)


class ReleaseError(Exception):
    pass


def pyproject_version(root: Path = ROOT) -> str:
    with open(root / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def package_version(root: Path = ROOT) -> str:
    text = (root / "src" / "smisim" / "__init__.py").read_text()
    match = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    if not match:
        raise ReleaseError("no __version__ in src/smisim/__init__.py")
    return match.group(1)


def changelog_sections(text: str) -> list[tuple[str, str]]:
    """Split CHANGELOG.md into (heading title, body) pairs for every '## ' heading."""
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            sections.append((m["title"], []))
        elif sections:
            sections[-1][1].append(line)
    return [(title, "\n".join(body).strip()) for title, body in sections]


def release_notes(changelog: str, version: str) -> str:
    for title, body in changelog_sections(changelog):
        m = RELEASE_TITLE.match(title)
        if m and m["version"] == version:
            if not body:
                raise ReleaseError(f"CHANGELOG.md section for {version} is empty")
            return body + "\n"
    raise ReleaseError(f"CHANGELOG.md has no '## {version} (YYYY-MM-DD)' section")


def check(tag: str, root: Path = ROOT) -> str:
    if not tag.startswith("v"):
        raise ReleaseError(f"release tags look like v1.2.3, got {tag!r}")
    version = tag[1:]
    for where, found in (
        ("pyproject.toml", pyproject_version(root)),
        ("src/smisim/__init__.py", package_version(root)),
    ):
        if found != version:
            raise ReleaseError(f"tag {tag} does not match {where} version {found}")
    return release_notes((root / "CHANGELOG.md").read_text(), version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="release tag, e.g. v0.3.0")
    parser.add_argument("--notes", type=Path, help="write the changelog section here")
    args = parser.parse_args(argv)
    try:
        notes = check(args.tag)
    except ReleaseError as exc:
        print(f"release check failed: {exc}", file=sys.stderr)
        return 1
    if args.notes:
        args.notes.write_text(notes)
    else:
        sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
