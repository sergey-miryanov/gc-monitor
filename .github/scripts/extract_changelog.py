#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
PYPROJECT_PATH = ROOT / "pyproject.toml"

VERSION_HEADER_RE = re.compile(r"^## Version (?P<version>\S+)", re.MULTILINE)


def resolve_version(tag: str | None) -> str:
    if tag and tag.startswith("v"):
        return tag[1:]
    with PYPROJECT_PATH.open("rb") as f:
        version: str = tomllib.load(f)["tool"]["poetry"]["version"]
    return version


def extract(version: str) -> str:
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    pattern = rf"## Version {re.escape(version)}(?=\s|$).*?\n(.*?)(?=\n## |\Z)"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    headers = VERSION_HEADER_RE.findall(text)
    print(f"::error::No changelog section for version {version!r}.", file=sys.stderr)
    print(f"::error::Found headers: {headers}", file=sys.stderr)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a version's section from CHANGELOG.md")
    parser.add_argument("tag", nargs="?", help="Release tag (e.g. v0.1.0); default = pyproject version")
    args = parser.parse_args()
    version = resolve_version(args.tag)
    body = extract(version)
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
