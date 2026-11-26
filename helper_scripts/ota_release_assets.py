"""Publish a build's OTA images as GitHub release assets and point the OTA index files at them.

patapovich fork, home-dimmer_build only (build.yml runs it after the firmware commit). GitHub
counts the downloads of release assets (download_count) but not those of raw files, which the
index named until now.

Zigbee2MQTT (zigbee-herdsman fetchFirmware) downloads an image with a plain fetch(), which
follows the 302 of github.com/<repo>/releases/download/<tag>/<file> to the asset host. The
asset is the same file, so the sha512 of the index entry still matches. Only the url fields
change: the index files stay where they are (users' Zigbee2MQTT settings name them).

Steps; each can be run again, nothing is overwritten:
1. the entries of the index files whose image is a raw link into this repo's bin/ (entries of
   other repos are left alone), each matched to its file in this checkout by name and sha512;
   with --bin-dir only those of one board's directory (every board names its files
   tlc_switch-<build>[-from_tuya|-forced].zigbee: run once per board, each with its own
   --tag-prefix);
2. one release per build, tag <prefix>-<build> at the commit the raw links pin (else --commit);
3. the files the release lacks are uploaded, then every asset's size and sha256 digest is
   checked against the file (GitHub's digest, so the check downloads nothing and counts nothing);
4. the url fields become the release download links (of the entries with the file's name and
   sha512 only: another board's file of the same name keeps its link);
5. with --keep N, releases of this prefix older than the N newest that nobody downloaded are
   deleted; their tags stay, so the commits (and old raw links) stay too.
"""
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import home_dimmer_changelog as hdc  # noqa: E402

RAW = re.compile(
    r"^https://(?:github\.com/(?P<o1>[^/]+)/(?P<r1>[^/]+)/raw/"
    r"|raw\.githubusercontent\.com/(?P<o2>[^/]+)/(?P<r2>[^/]+)/)"
    r"(?P<ref>[0-9a-f]{40}|refs/heads/.+?)/(?P<path>bin/.+)$")
FILE = re.compile(r"^.+-(?P<build>[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{7,16})"
                  r"(?:-(?P<kind>from_tuya|forced))?\.zigbee$")
CARRIES = re.compile(r"own MCU firmware: version ([0-9]+\.[0-9]+\.[0-9]+)")
KINDS = {
    None: "update from an earlier build of this firmware",
    "from_tuya": "conversion from the Tuya stock firmware",
    "forced": "the same firmware with OTA version 0xFFFFFFFF, for the force index",
}


def release_url(repo: str, tag: str, name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{name}"


@dataclass
class Entry:
    index: Path
    name: str
    sha512: str
    file: Path
    pinned: str | None  # the commit a frozen raw link names


def repo_of(m: re.Match) -> str:
    return f"{m['o1'] or m['o2']}/{m['r1'] or m['r2']}"


def under(rel: str, bin_dir: str | None) -> bool:
    """rel (a path in the checkout) is in bin_dir (None: anywhere)."""
    return bin_dir is None or rel.startswith(bin_dir.strip("/") + "/")


def collect(index_files: list[Path], repo: str, root: Path, tag_prefix: str,
            bin_dir: str | None = None) -> list[Entry]:
    """The entries to publish: raw links into this repo (into bin_dir only, when given), or
    (run again) its release links of this prefix, their file looked for in bin_dir."""
    ours = f"https://github.com/{repo}/releases/download/{tag_prefix}-"
    where = bin_dir.strip("/") if bin_dir else "bin"
    entries = []
    for index in index_files:
        for meta in json.loads(index.read_text()):
            url, name = meta.get("url", ""), meta["fileName"]
            m = RAW.match(url)
            if m and repo_of(m).lower() == repo.lower():
                if not under(m["path"], bin_dir):
                    continue  # another board's image
                pinned = m["ref"] if re.fullmatch(r"[0-9a-f]{40}", m["ref"]) else None
                rel = m["path"]
            elif url.startswith(ours):
                pinned, rel = None, None
            else:
                continue
            files = [root / rel] if rel else sorted(root.glob(f"{where}/**/{name}"))
            files = [f for f in files if f.is_file()]
            if len(files) != 1 or files[0].name != name:
                raise SystemExit(f"{index}: no single file {name} in {root / where}")
            if hashlib.sha512(files[0].read_bytes()).hexdigest() != meta["sha512"]:
                raise SystemExit(f"{index}: {files[0]} does not match the sha512 of {name}")
            entries.append(Entry(index, name, meta["sha512"], files[0], pinned))
    return entries


def build_of(entries: list[Entry]) -> str:
    builds = set()
    for e in entries:
        m = FILE.match(e.name)
        if not m:
            raise SystemExit(f"{e.index}: {e.name} is not <name>-<build>[-from_tuya|-forced].zigbee")
        builds.add(m["build"])
    if len(builds) != 1:
        raise SystemExit(f"the index files name more than one build: {sorted(builds)}")
    return builds.pop()


def pinned_of(entries: list[Entry]) -> str | None:
    pinned = {e.pinned for e in entries if e.pinned}
    if len(pinned) > 1:
        raise SystemExit(f"the raw links pin more than one commit: {sorted(pinned)}")
    return pinned.pop() if pinned else None


def rewrite(text: str, urls: dict[tuple[str, str], str]) -> str:
    """The index text with the url of each file (name, sha512) replaced, written as the generator
    does. By the sha512 too: every board's files have the same names."""
    data = json.loads(text)
    for meta in data:
        key = (meta["fileName"], meta.get("sha512"))
        if key in urls:
            meta["url"] = urls[key]
    out = json.dumps(data, indent=2)
    return out + "\n" if text.endswith("\n") else out


def to_prune(releases: list[dict], tag_prefix: str, keep: int, current: str) -> list[str]:
    """Tags of the releases to delete: beyond the `keep` newest of the prefix, never downloaded."""
    mine = [r for r in releases if r["tag_name"].startswith(tag_prefix + "-") and not r.get("draft")]
    mine.sort(key=lambda r: r["created_at"], reverse=True)
    return [r["tag_name"] for r in mine[keep:]
            if r["tag_name"] != current and sum(a["download_count"] for a in r["assets"]) == 0]


def notes(build: str, name: str, carries: str | None, files: list[str], changes: list[str],
          index_url: str | None, links: list[str]) -> str:
    what = f"Module firmware build {build} for the {name}"
    what += f", carrying Open Dimmer {carries} for its dimming MCU." if carries else "."
    out = [what, ""]
    if index_url:
        out += ["Zigbee2MQTT downloads these files through the OTA index", f"<{index_url}>,",
                "so there is no need to download them by hand.", ""]
    for f in sorted(files, key=lambda f: (FILE.match(f)["kind"] or "", f)):
        out.append(f"- `{f}`: {KINDS[FILE.match(f)['kind']]}")
    if changes:
        out += ["", "Changes in this build:", ""] + changes
    if links:
        out += [""] + [f"- {link}" for link in links]
    return "\n".join(out) + "\n"


def changes_of(changelog: Path | None, build: str) -> list[str]:
    """The build's changelog section, or "Next" (the build branch is cut before the stamp)."""
    if not changelog or not changelog.is_file():
        return []
    text = changelog.read_text()
    sec = hdc.section(text, build)
    if sec is not None:
        return hdc.bullets(sec.splitlines()[1:])
    for heading, body in hdc.split(text)[1]:
        if heading == hdc.NEXT:
            return hdc.bullets(body)
    return []


def carries_of(image_c: Path | None) -> str | None:
    if not image_c or not image_c.is_file():
        return None
    m = CARRIES.search(image_c.read_text(errors="replace")[:4000])
    return m.group(1) if m else None


class GitHub:
    """The few gh calls this needs (gh reads GH_TOKEN)."""

    def __init__(self, repo: str, dry_run: bool = False):
        self.repo, self.dry_run = repo, dry_run

    def _gh(self, *args: str, write: bool = False) -> str:
        if write and self.dry_run:
            print("dry run: gh " + " ".join(args))
            return ""
        r = subprocess.run(["gh", *args], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout

    def release(self, tag: str) -> dict | None:
        try:
            return json.loads(self._gh("api", f"repos/{self.repo}/releases/tags/{tag}"))
        except RuntimeError as e:
            if "HTTP 404" in str(e):
                return None
            raise

    def tag_commit(self, tag: str) -> str | None:
        try:
            ref = json.loads(self._gh("api", f"repos/{self.repo}/git/ref/tags/{tag}"))
        except RuntimeError as e:
            if "HTTP 404" in str(e):
                return None
            raise
        obj = ref["object"]
        if obj["type"] == "tag":  # annotated
            obj = json.loads(self._gh("api", f"repos/{self.repo}/git/tags/{obj['sha']}"))["object"]
        return obj["sha"]

    def create(self, tag: str, target: str, title: str, body: str, files: list[Path]) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
            f.write(body)
            f.flush()
            self._gh("release", "create", tag, "--repo", self.repo, "--target", target,
                     "--title", title, "--notes-file", f.name, *map(str, files), write=True)

    def upload(self, tag: str, files: list[Path]) -> None:
        self._gh("release", "upload", tag, "--repo", self.repo, *map(str, files), write=True)

    def releases(self) -> list[dict]:
        out = self._gh("api", "--paginate", f"repos/{self.repo}/releases?per_page=100", "--jq",
                       ".[] | {tag_name, created_at, draft, assets: [.assets[] | {download_count}]}")
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def delete(self, tag: str) -> None:
        # the tag stays: its commit (and raw links into it) are kept
        self._gh("release", "delete", tag, "--repo", self.repo, "--yes", write=True)


def check_assets(release: dict, files: list[Path]) -> list[str]:
    """What is wrong with the release's copies of the files (empty: all match)."""
    assets = {a["name"]: a for a in release.get("assets", [])}
    bad = []
    for f in files:
        a = assets.get(f.name)
        data = f.read_bytes()
        if a is None:
            bad.append(f"{f.name}: missing")
        elif a.get("state", "uploaded") != "uploaded" or a["size"] != len(data):
            bad.append(f"{f.name}: size {a['size']} ({a.get('state')}), file {len(data)}")
        elif not a.get("digest"):
            bad.append(f"{f.name}: no digest yet")
        elif a["digest"] != "sha256:" + hashlib.sha256(data).hexdigest():
            bad.append(f"{f.name}: digest {a['digest']} differs from the file")
    return bad


def main(argv: list[str], gh: GitHub | None = None, root: Path = Path(".")) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("index_files", nargs="+", type=Path)
    ap.add_argument("--repo", required=True, help="owner/repo of the releases and raw links")
    ap.add_argument("--tag-prefix", required=True, help="the release tags are <prefix>-<build>")
    ap.add_argument("--name", required=True, help="the device, for the release text")
    ap.add_argument("--commit", help="tag target when the links pin no commit")
    ap.add_argument("--keep", type=int, help="prune undownloaded releases beyond the N newest")
    ap.add_argument("--index-url", help="the index users point Zigbee2MQTT at, for the text")
    ap.add_argument("--link", action="append", default=[], help="extra line for the text")
    ap.add_argument("--changelog", type=Path, default=Path("docs/changelog_home_dimmer.md"))
    ap.add_argument("--carried-image", default="src/zigbee/secondary_mcu_image.c",
                    help="the MCU image the firmware carries, for the text (\"\": none, e.g. the TS110E)")
    ap.add_argument("--bin-dir", help="only the images of this board's directory, e.g. "
                                      "bin/router/MODULE_TUYA_TS110E_3MPWQZUU")
    ap.add_argument("--dry-run", action="store_true", help="change nothing, print the gh writes")
    args = ap.parse_args(argv)
    gh = gh or GitHub(args.repo, args.dry_run)

    entries = collect(args.index_files, args.repo, root, args.tag_prefix, args.bin_dir)
    if not entries:
        print("no image of this repo in the index files: nothing to publish")
        return 0
    build = build_of(entries)
    tag = f"{args.tag_prefix}-{build}"
    files = sorted({e.file for e in entries})
    if len({f.name for f in files}) != len(files):
        raise SystemExit("two different files of the same name")

    # the commit the raw links pin; links already moved to the release (a run again) keep its tag
    pinned, have = pinned_of(entries), gh.tag_commit(tag)
    if pinned and have and have != pinned:
        raise SystemExit(f"tag {tag} is at {have}, the images are from {pinned}")
    target = pinned or have or args.commit
    if not target:
        raise SystemExit("the links pin no commit: give --commit")
    release = gh.release(tag)
    if release is None:
        carries = carries_of(root / args.carried_image if args.carried_image else None)
        title = f"{args.name} {build}" + (f" (Open Dimmer {carries})" if carries else "")
        body = notes(build, args.name, carries, [f.name for f in files],
                     changes_of(root / args.changelog, build), args.index_url, args.link)
        print(f"release {tag} at {target[:12]}: {', '.join(f.name for f in files)}")
        gh.create(tag, target, title, body, files)
    else:
        names = {a["name"] for a in release.get("assets", [])}
        missing = [f for f in files if f.name not in names]
        if missing:
            print(f"release {tag}: uploading {', '.join(f.name for f in missing)}")
            gh.upload(tag, missing)
        else:
            print(f"release {tag}: has every file")
    if args.dry_run:
        return 0

    bad = ["not found"]
    for _ in range(10):
        release = gh.release(tag)
        bad = check_assets(release, files) if release else ["release not found"]
        if not bad or not all(b.endswith("no digest yet") for b in bad):
            break
        time.sleep(3)
    if bad:
        raise SystemExit(f"release {tag} does not match the files: {'; '.join(bad)}")
    print(f"release {tag}: {len(files)} asset(s) match the files (size, sha256)")

    urls = {(e.name, e.sha512): release_url(args.repo, tag, e.name) for e in entries}
    for index in args.index_files:
        text = index.read_text()
        new = rewrite(text, urls)
        if new != text:
            index.write_text(new)
            print(f"{index}: links now point at release {tag}")

    if args.keep is not None:
        for old in to_prune(gh.releases(), args.tag_prefix, args.keep, tag):
            print(f"release {old}: never downloaded, beyond the {args.keep} newest: deleted (tag kept)")
            gh.delete(old)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
