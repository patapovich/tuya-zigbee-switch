"""Download counts of the ZDMS16-2 module firmware releases, as a page of this fork.

patapovich fork only (.github/workflows/ota-downloads.yml). build.yml on home-dimmer_build makes
one release per module firmware build (tag zdms16-2-<build>) and points the public Zigbee2MQTT
OTA index at its assets, so GitHub's download_count of an asset = the downloads of that image.
This writes, into the checkout of the ota-downloads branch:

- README.md: totals and a table per build;
- history.csv: one row (UTC time, totals) whenever a total changed.

Nothing is rewritten when no count changed, so the branch gets a commit only on a change.

    ota_downloads.py <releases.json> <out dir> --repo owner/repo [--now "2026-11-20 18:00"]

<releases.json>: the releases as GitHub's API lists them (a JSON list).
"""
import argparse
import csv
import datetime
import io
import json
import re
import sys
from pathlib import Path

PREFIX = "zdms16-2-"
KINDS = ("updates", "conversions", "forced")
HISTORY_HEAD = ["time_utc", *KINDS]
CARRIES = re.compile(r"\(Open Dimmer ([0-9]+\.[0-9]+\.[0-9]+)\)")
# Downloads that were not Zigbee2MQTT installs by others.
NOTES = [
    "2026-11-20: the first downloads of 1.1.3-2b74dd50 (updates 2, conversions 1, forced 1) were "
    "one test and one fetch of every asset (a person or a crawler on the release page).",
]


def kind(asset_name: str) -> str | None:
    if not asset_name.endswith(".zigbee"):
        return None
    if asset_name.endswith("-from_tuya.zigbee"):
        return "conversions"
    if asset_name.endswith("-forced.zigbee"):
        return "forced"
    return "updates"


def builds(releases: list[dict]) -> list[dict]:
    """One row per module firmware release, newest first."""
    rows = []
    for r in releases:
        tag = r.get("tag_name") or ""
        if not tag.startswith(PREFIX) or r.get("draft"):
            continue
        created = r.get("created_at") or ""
        row = {"build": tag[len(PREFIX):], "tag": tag, "created": created, "date": created[:10],
               "carries": "", **{k: 0 for k in KINDS}}
        m = CARRIES.search(r.get("name") or "")
        if m:
            row["carries"] = m.group(1)
        for a in r.get("assets", []):
            k = kind(a.get("name", ""))
            if k:
                row[k] += int(a.get("download_count", 0))
        rows.append(row)
    rows.sort(key=lambda row: (row["created"], row["tag"]), reverse=True)
    return rows


def totals(rows: list[dict]) -> dict[str, int]:
    return {k: sum(row[k] for row in rows) for k in KINDS}


def read_history(path: Path) -> list[list[str]]:
    if not path.is_file():
        return []
    return [r for r in csv.reader(io.StringIO(path.read_text())) if r][1:]


def history_text(rows: list[list[str]]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(HISTORY_HEAD)
    w.writerows(rows)
    return out.getvalue()


def page(repo: str, rows: list[dict], tot: dict[str, int], as_of: str) -> str:
    base = f"https://github.com/{repo}"
    out = [
        "# ZDMS16-2 module firmware downloads",
        "",
        "How often Zigbee2MQTT has downloaded the AVATTO ZDMS16-2 module firmware of this fork (it",
        "carries [Open Dimmer](https://github.com/patapovich/open-dimmer) for the dimming MCU). Every",
        f"build is a [release]({base}/releases) (tag `{PREFIX}<build>`) and the OTA index",
        "points at its files, so these are GitHub's download counts of those files.",
        "",
        "- **Updates**: the normal image, an update from an earlier build of this firmware.",
        "- **Conversions**: the `-from_tuya` image, a module converted from the Tuya stock firmware.",
        "- **Forced**: the `-forced` image of the force index.",
        "",
        "| | Downloads |",
        "|---|---:|",
        f"| Updates | {tot['updates']} |",
        f"| Conversions | {tot['conversions']} |",
        f"| Forced | {tot['forced']} |",
        "",
        f"Counts as of {as_of} UTC (the last change; checked every hour by `.github/workflows/"
        "ota-downloads.yml` on `main`, GitHub's counts lag a few minutes). History:",
        "[history.csv](history.csv).",
        "",
        "## Per build",
        "",
        "Newest first. Builds nobody downloaded are deleted beyond the 10 newest.",
        "",
        "| Build | Released | Open Dimmer | Updates | Conversions | Forced |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in rows:
        out.append(f"| [{row['build']}]({base}/releases/tag/{row['tag']}) | {row['date']} | "
                   f"{row['carries'] or '-'} | {row['updates']} | {row['conversions']} | {row['forced']} |")
    if NOTES:
        out += ["", "## Notes", ""] + [f"- {n}" for n in NOTES]
    return "\n".join(out) + "\n"


def update(releases: list[dict], out: Path, repo: str, now: str) -> bool:
    """Write the page and history; True when anything changed."""
    rows = builds(releases)
    tot = totals(rows)
    hist_path, page_path = out / "history.csv", out / "README.md"
    hist = read_history(hist_path)
    last = [int(x) for x in hist[-1][1:]] if hist else None
    changed_totals = last != [tot[k] for k in KINDS]
    if changed_totals:
        hist.append([now, *(str(tot[k]) for k in KINDS)])
    as_of = hist[-1][0] if hist else now
    new_page = page(repo, rows, tot, as_of)
    old_page = page_path.read_text() if page_path.is_file() else None
    if not changed_totals and new_page == old_page:
        return False
    out.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(history_text(hist))
    page_path.write_text(new_page)
    return True


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("releases", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--now", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    help="UTC time for a history row")
    args = ap.parse_args(argv)
    releases = json.loads(args.releases.read_text())
    changed = update(releases, args.out, args.repo, args.now)
    tot = totals(builds(releases))
    print(("changed: " if changed else "unchanged: ") + ", ".join(f"{k} {tot[k]}" for k in KINDS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
