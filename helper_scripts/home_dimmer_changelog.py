#!/usr/bin/env python3
"""The build changelog of the home-dimmer branch (docs/changelog_home_dimmer.md).

  home_dimmer_changelog.py stamp --build 1.1.3-<id> --carries X.Y.Z [--date YYYY-MM-DD] [FILE]
      turns "## Next" into the section of that build ("## 1.1.3-<id> (date), carries Open
      Dimmer X.Y.Z") and opens an empty "## Next" above it. When the carried version differs from
      the one of the build before, the section starts with a "Carries Open Dimmer X.Y.Z" bullet;
      a build with nothing else new says it was rebuilt. A build that already has its section is
      left as it is (exit 0).
  home_dimmer_changelog.py section BUILD [FILE]
      prints the section of a build: "1.1.3-<id>" or the id alone, also shortened to its first
      7 characters (git may abbreviate a hash differently in another clone). Exit 1 if none.

py32-bump (the fork's default branch) runs stamp right after it has cut home-dimmer_build, as
the build's id is the hash of that branch's head (board.mk VERSION_STR)."""
import argparse
import datetime
import re
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / "docs" / "changelog_home_dimmer.md"
NEXT = "## Next"
HEADING = re.compile(r"^## (\S+) \(([0-9-]+)\)(?:, carries (?:Open Dimmer (\S+)|no Open Dimmer image))?\s*$")
BUILD = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{7,16}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def split(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The lines before the first section, and every section as (heading, body lines)."""
    head, sections = [], []
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line.rstrip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            head.append(line)
    return head, sections


def bullets(body: list[str]) -> list[str]:
    """The body without its leading and trailing blank lines."""
    lines = list(body)
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def build_id(heading: str) -> str | None:
    m = HEADING.match(heading)
    return m.group(1) if m else None


def matches(heading: str, build: str) -> bool:
    """Does this heading name the build ("1.1.3-<id>" or the id, possibly abbreviated)?"""
    hid = build_id(heading)
    if hid is None:
        return False
    want = build.split("-", 1)[1] if "-" in build else build
    have = hid.split("-", 1)[1] if "-" in hid else hid
    if len(want) < 7 or len(have) < 7:
        return False
    n = min(len(want), len(have))
    return want[:n] == have[:n] and ("-" not in build or build.split("-", 1)[0] == hid.split("-", 1)[0])


def stamp(text: str, build: str, carries: str, date: str) -> str:
    if not BUILD.match(build):
        raise ValueError(f"not a build 1.1.3-<id>: {build!r}")
    if not VERSION.match(carries):
        raise ValueError(f"not an Open Dimmer version X.Y.Z: {carries!r}")
    head, sections = split(text)
    if any(matches(h, build) for h, _ in sections):
        return text
    nexts = [i for i, (h, _) in enumerate(sections) if h == NEXT]
    if len(nexts) != 1:
        raise ValueError("the changelog needs exactly one '## Next' section")
    i = nexts[0]
    body = bullets(sections[i][1])
    before = next((HEADING.match(h) for h, _ in sections[i + 1:] if HEADING.match(h)), None)
    carried_before = before.group(3) if before else None
    if carried_before != carries:
        body.insert(0, f"- Carries Open Dimmer {carries}.")
    if not body:
        body = [f"- Rebuilt, nothing new; carries Open Dimmer {carries}."]
    sections[i:i + 1] = [(NEXT, [""]),
                         (f"## {build} ({date}), carries Open Dimmer {carries}", [""] + body + [""])]
    out = list(head)
    for h, b in sections:
        out.append(h)
        out.extend(b)
    return "\n".join(out).rstrip("\n") + "\n"


def section(text: str, build: str) -> str | None:
    for h, body in split(text)[1]:
        if matches(h, build):
            return "\n".join([h, ""] + bullets(body))
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stamp")
    s.add_argument("--build", required=True)
    s.add_argument("--carries", required=True)
    s.add_argument("--date", default=datetime.date.today().isoformat())
    s.add_argument("file", nargs="?", type=Path, default=DEFAULT)
    q = sub.add_parser("section")
    q.add_argument("build")
    q.add_argument("file", nargs="?", type=Path, default=DEFAULT)
    a = ap.parse_args(argv)
    text = a.file.read_text(encoding="utf-8")
    if a.cmd == "stamp":
        try:
            new = stamp(text, a.build, a.carries, a.date)
        except ValueError as e:
            print(f"home_dimmer_changelog: {e}", file=sys.stderr)
            return 2
        if new == text:
            print(f"{a.build} already has its section")
        else:
            a.file.write_text(new, encoding="utf-8")
            print(f"{a.build}: section written")
        return 0
    found = section(text, a.build)
    if found is None:
        print(f"no section for {a.build}", file=sys.stderr)
        return 1
    print(found)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
