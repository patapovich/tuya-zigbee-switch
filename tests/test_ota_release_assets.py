"""helper_scripts/ota_release_assets.py: a build's OTA images as release assets, the index files
pointing at them (build.yml of home-dimmer_build). GitHub is faked; nothing goes out."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "helper_scripts" / "ota_release_assets.py"

spec = importlib.util.spec_from_file_location("ota_release_assets", SCRIPT)
ora = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ora)

REPO = "someone/tuya-zigbee-switch"
PIN = "f59fbb798d0d2b4b0419ead1ecf594e450402f16"
BUILD = "1.1.3-69efbba2"
TAG = "zdms16-2-" + BUILD
BOARD_DIR = "bin/router/MODULE_X"
NAMES = [f"tlc_switch-{BUILD}.zigbee", f"tlc_switch-{BUILD}-from_tuya.zigbee"]
FORCED = f"tlc_switch-{BUILD}-forced.zigbee"
OTHER = ("https://github.com/upstream/tuya-zigbee-switch/raw/37f72be3501cc5f691f857dfa6dc6e52a7c39e51/"
         "bin/router/OTHER/tlc_switch-1.1.3-ec57f81b.zigbee")


def entry(name: str, data: bytes, url: str) -> dict:
    return {"fileName": name, "fileVersion": 1, "fileSize": len(data), "url": url, "imageType": 2,
            "manufacturerCode": 4417, "sha512": hashlib.sha512(data).hexdigest(),
            "otaHeaderString": "Telink OTA Image\u0000\u0000"}


def raw(name: str, ref: str = PIN) -> str:
    return f"https://github.com/{REPO}/raw/{ref}/{BOARD_DIR}/{name}"


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A checkout after the build: three images, two index files (one with an upstream entry)."""
    (tmp_path / BOARD_DIR).mkdir(parents=True)
    ota = tmp_path / "zigbee2mqtt" / "ota"
    ota.mkdir(parents=True)
    router = []
    for n in NAMES:
        data = b"\x1e\xf1\xee\x0b" + n.encode()
        (tmp_path / BOARD_DIR / n).write_bytes(data)
        router.append(entry(n, data, raw(n)))
    router.append(entry("tlc_switch-1.1.3-ec57f81b.zigbee", b"other", OTHER))
    data = b"\x1e\xf1\xee\x0bforced"
    (tmp_path / BOARD_DIR / FORCED).write_bytes(data)
    (ota / "index_router.json").write_text(json.dumps(router, indent=2))
    (ota / "index_router-FORCE.json").write_text(json.dumps([entry(FORCED, data, raw(FORCED))], indent=2))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "changelog_home_dimmer.md").write_text(
        "# home-dimmer builds\n\nIntro.\n\n## Next\n\n- A change,\n  wrapped.\n\n"
        "## 1.1.3-aa9ab5d4 (2026-11-20), carries Open Dimmer 2.2.4\n\n- Older.\n")
    (tmp_path / "src" / "zigbee").mkdir(parents=True)
    (tmp_path / "src" / "zigbee" / "secondary_mcu_image.c").write_text(
        "/*\n * PY32 app image of the AVATTO ZDMS16-2 own MCU firmware: version 2.2.5\n */\n")
    return tmp_path


class FakeGitHub:
    """Releases and tags in memory; uploads get GitHub's sha256 digest."""

    def __init__(self, files: dict[str, bytes] | None = None):
        self.releases_ = {}  # tag -> release dict
        self.tags = {}
        self.calls = []
        self.corrupt = set()
        self.clock = 0

    def _asset(self, f: Path) -> dict:
        data = f.read_bytes()
        if f.name in self.corrupt:  # same size, other bytes
            data = data[:-1] + bytes([data[-1] ^ 1])
        return {"name": f.name, "size": len(data), "state": "uploaded", "download_count": 0,
                "digest": "sha256:" + hashlib.sha256(data).hexdigest()}

    def release(self, tag):
        return json.loads(json.dumps(self.releases_[tag])) if tag in self.releases_ else None

    def tag_commit(self, tag):
        return self.tags.get(tag)

    def create(self, tag, target, title, body, files):
        self.calls.append(("create", tag, target, [f.name for f in files]))
        self.tags.setdefault(tag, target)
        self.clock += 1
        self.releases_[tag] = {"tag_name": tag, "title": title, "body": body, "draft": False,
                               "created_at": f"2026-11-20T00:00:{self.clock:02d}Z",
                               "assets": [self._asset(f) for f in files]}

    def upload(self, tag, files):
        self.calls.append(("upload", tag, [f.name for f in files]))
        self.releases_[tag]["assets"] += [self._asset(f) for f in files]

    def releases(self):
        return [self.release(t) for t in self.releases_]

    def delete(self, tag):
        self.calls.append(("delete", tag))
        del self.releases_[tag]


def run(tree: Path, gh: FakeGitHub, *extra: str) -> int:
    idx = [str(tree / "zigbee2mqtt/ota/index_router.json"), str(tree / "zigbee2mqtt/ota/index_router-FORCE.json")]
    return ora.main([*idx, "--repo", REPO, "--tag-prefix", "zdms16-2", "--name", "AVATTO ZDMS16-2",
                     "--index-url", "https://example.invalid/index_router.json", *extra],
                    gh=gh, root=tree)


def index(tree: Path, name: str = "index_router.json") -> list[dict]:
    return json.loads((tree / "zigbee2mqtt/ota" / name).read_text())


def test_publishes_one_release_at_the_pinned_commit_and_moves_only_our_links(tree):
    before = index(tree)
    gh = FakeGitHub()
    assert run(tree, gh) == 0
    assert gh.calls == [("create", TAG, PIN, sorted(NAMES + [FORCED]))]
    after = index(tree)
    assert [e["url"] for e in after] == [
        f"https://github.com/{REPO}/releases/download/{TAG}/{NAMES[0]}",
        f"https://github.com/{REPO}/releases/download/{TAG}/{NAMES[1]}",
        OTHER]
    for b, a in zip(before, after):  # nothing but the url changes, the order neither
        assert list(a) == list(b)
        assert {k: v for k, v in a.items() if k != "url"} == {k: v for k, v in b.items() if k != "url"}
    assert index(tree, "index_router-FORCE.json")[0]["url"].endswith(f"/releases/download/{TAG}/{FORCED}")


def test_the_pinned_commit_wins_over_the_given_one(tree):
    gh = FakeGitHub()
    run(tree, gh, "--commit", "3" * 40)  # build.yml gives HEAD, the freeze commit after it
    assert gh.calls[0][:3] == ("create", TAG, PIN)


def test_release_text_names_the_build_files_carried_version_and_next_changes(tree):
    gh = FakeGitHub()
    run(tree, gh, "--link", "Install guide: https://example.invalid/INSTALL.md")
    rel = gh.releases_[TAG]
    assert rel["title"] == f"AVATTO ZDMS16-2 {BUILD} (Open Dimmer 2.2.5)"
    body = rel["body"]
    assert f"Module firmware build {BUILD} for the AVATTO ZDMS16-2, carrying Open Dimmer 2.2.5" in body
    assert f"- `{NAMES[0]}`: update from an earlier build" in body
    assert f"- `{NAMES[1]}`: conversion from the Tuya stock firmware" in body
    assert f"- `{FORCED}`: the same firmware with OTA version 0xFFFFFFFF" in body
    assert "Changes in this build:\n\n- A change,\n  wrapped.\n" in body
    assert "Older." not in body
    assert "- Install guide: https://example.invalid/INSTALL.md" in body
    assert "/home/" not in body


def test_stamped_section_wins_over_next(tree):
    cl = tree / "docs" / "changelog_home_dimmer.md"
    cl.write_text(cl.read_text().replace(
        "## Next\n\n- A change,\n  wrapped.\n",
        f"## Next\n\n- Later.\n\n## {BUILD} (2026-11-20), carries Open Dimmer 2.2.5\n\n- Stamped.\n"))
    gh = FakeGitHub()
    run(tree, gh)
    assert "- Stamped." in gh.releases_[TAG]["body"] and "Later." not in gh.releases_[TAG]["body"]


def test_run_again_changes_nothing_and_a_lost_asset_is_uploaded_again(tree):
    gh = FakeGitHub()
    run(tree, gh)
    text = (tree / "zigbee2mqtt/ota/index_router.json").read_text()
    gh.calls.clear()
    assert run(tree, gh, "--commit", "0" * 40) == 0  # links point at the release: its tag stays
    assert gh.calls == []
    assert (tree / "zigbee2mqtt/ota/index_router.json").read_text() == text
    gh.releases_[TAG]["assets"] = [a for a in gh.releases_[TAG]["assets"] if a["name"] != FORCED]
    run(tree, gh)
    assert gh.calls == [("upload", TAG, [FORCED])]


def test_a_differing_asset_stops_before_the_index_moves(tree):
    gh = FakeGitHub()
    gh.corrupt.add(NAMES[1])
    before = (tree / "zigbee2mqtt/ota/index_router.json").read_text()
    with pytest.raises(SystemExit, match="does not match the files"):
        run(tree, gh)
    assert (tree / "zigbee2mqtt/ota/index_router.json").read_text() == before


def test_tag_at_another_commit_is_refused(tree):
    gh = FakeGitHub()
    gh.tags[TAG] = "1" * 40
    with pytest.raises(SystemExit, match="is at 1111"):
        run(tree, gh)
    assert gh.calls == []


def test_a_file_that_does_not_match_its_sha512_is_refused(tree):
    (tree / BOARD_DIR / NAMES[0]).write_bytes(b"\x1e\xf1\xee\x0bchanged")
    with pytest.raises(SystemExit, match="does not match the sha512"):
        run(tree, FakeGitHub())


def test_two_builds_in_the_index_are_refused(tree):
    f = tree / "zigbee2mqtt/ota/index_router-FORCE.json"
    data = json.loads(f.read_text())
    other = "tlc_switch-1.1.3-12345678-forced.zigbee"
    (tree / BOARD_DIR / FORCED).rename(tree / BOARD_DIR / other)
    data[0]["fileName"], data[0]["url"] = other, raw(other)
    f.write_text(json.dumps(data, indent=2))
    with pytest.raises(SystemExit, match="more than one build"):
        run(tree, FakeGitHub())


def test_branch_links_need_a_commit(tree):
    for name in ("index_router.json", "index_router-FORCE.json"):
        f = tree / "zigbee2mqtt/ota" / name
        f.write_text(f.read_text().replace(f"/raw/{PIN}/", "/raw/refs/heads/home-dimmer_build/"))
    with pytest.raises(SystemExit, match="give --commit"):
        run(tree, FakeGitHub())
    gh = FakeGitHub()
    run(tree, gh, "--commit", "2" * 40)
    assert gh.calls[0][:3] == ("create", TAG, "2" * 40)


def test_raw_githubusercontent_links_are_ours_too(tree):
    f = tree / "zigbee2mqtt/ota/index_router.json"
    f.write_text(f.read_text().replace(f"https://github.com/{REPO}/raw/", f"https://raw.githubusercontent.com/{REPO}/"))
    gh = FakeGitHub()
    run(tree, gh)
    assert all("/releases/download/" in e["url"] for e in index(tree)[:2])


def test_prune_keeps_the_newest_the_downloaded_and_the_current():
    def rel(tag, t, counts):
        return {"tag_name": tag, "created_at": f"2026-11-{t:02d}T00:00:00Z", "draft": False,
                "assets": [{"download_count": c} for c in counts]}
    releases = [rel(f"zdms16-2-1.1.3-{i:08x}", i, [0, 0]) for i in range(1, 8)]
    releases[1]["assets"][1]["download_count"] = 3        # downloaded: kept for its count
    releases.append(rel("1.1.3", 1, [0]))                   # upstream-style tag: not ours
    releases.append(rel("zdms16-2-1.1.3-000000aa", 0, [0]))  # current, even if oldest
    out = ora.to_prune(releases, "zdms16-2", 3, "zdms16-2-1.1.3-000000aa")
    assert out == [f"zdms16-2-1.1.3-{i:08x}" for i in (4, 3, 1)]


def test_main_prunes_after_publishing(tree):
    gh = FakeGitHub()
    for i in range(3):
        gh.releases_[f"zdms16-2-1.1.3-0000000{i}"] = {
            "tag_name": f"zdms16-2-1.1.3-0000000{i}", "draft": False,
            "created_at": f"2026-11-19T00:00:0{i}Z", "assets": [{"download_count": 0}]}
    run(tree, gh, "--keep", "2")
    assert [c for c in gh.calls if c[0] == "delete"] == [("delete", "zdms16-2-1.1.3-00000001"),
                                                          ("delete", "zdms16-2-1.1.3-00000000")]
    assert TAG in gh.releases_


def test_rewrite_keeps_the_generator_format_of_the_real_index_files():
    for f in sorted((ROOT / "zigbee2mqtt" / "ota").glob("*.json")):
        text = f.read_text()
        assert ora.rewrite(text, {}) == text, f


def test_nothing_of_ours_in_the_index_is_a_no_op(tree):
    for name in ("index_router.json", "index_router-FORCE.json"):
        (tree / "zigbee2mqtt/ota" / name).write_text(json.dumps([entry("x.zigbee", b"other", OTHER)], indent=2))
    gh = FakeGitHub()
    assert run(tree, gh) == 0
    assert gh.calls == []


# ---- two boards in one index (their files have the same names) ------------------------------

HK_DIR = "bin/router/MODULE_TS110E"
HK_TAG = "ts110e-3mpwqzuu-" + BUILD


def add_second_board(tree: Path) -> None:
    """The TS110E's images next to the first board's: the same names, other bytes, in both
    index files."""
    (tree / HK_DIR).mkdir(parents=True)
    for index_name, names in (("index_router.json", NAMES), ("index_router-FORCE.json", [FORCED])):
        f = tree / "zigbee2mqtt/ota" / index_name
        data = json.loads(f.read_text())
        for n in names:
            body = b"\x1e\xf1\xee\x0bts110e " + n.encode()
            (tree / HK_DIR / n).write_bytes(body)
            data.append(entry(n, body, f"https://github.com/{REPO}/raw/{PIN}/{HK_DIR}/{n}"))
        f.write_text(json.dumps(data, indent=2))


def run_board(tree: Path, gh: FakeGitHub, bin_dir: str, prefix: str, name: str, *extra: str) -> int:
    idx = [str(tree / "zigbee2mqtt/ota/index_router.json"), str(tree / "zigbee2mqtt/ota/index_router-FORCE.json")]
    return ora.main([*idx, "--repo", REPO, "--tag-prefix", prefix, "--name", name, "--bin-dir", bin_dir, *extra],
                    gh=gh, root=tree)


def urls(tree: Path, name: str = "index_router.json") -> list[str]:
    return [e["url"] for e in index(tree, name)]


def test_two_boards_without_a_bin_dir_are_refused_before_anything_goes_out(tree):
    add_second_board(tree)
    gh = FakeGitHub()
    with pytest.raises(SystemExit, match="two different files of the same name"):
        run(tree, gh)
    assert gh.calls == []


def test_each_board_gets_its_own_release_and_only_its_own_links_move(tree):
    add_second_board(tree)
    gh = FakeGitHub()
    assert run_board(tree, gh, BOARD_DIR, "zdms16-2", "AVATTO ZDMS16-2") == 0
    assert gh.calls == [("create", TAG, PIN, sorted(NAMES + [FORCED]))]
    after = urls(tree)
    assert after[:2] == [ora.release_url(REPO, TAG, n) for n in NAMES]
    assert after[2] == OTHER
    assert after[3:] == [f"https://github.com/{REPO}/raw/{PIN}/{HK_DIR}/{n}" for n in NAMES]  # not moved

    gh.calls.clear()
    assert run_board(tree, gh, HK_DIR, "ts110e-3mpwqzuu", "Tuya TS110E (ZH-Z06)", "--carried-image", "") == 0
    assert gh.calls == [("create", HK_TAG, PIN, sorted(NAMES + [FORCED]))]
    rel = gh.releases_[HK_TAG]
    assert rel["title"] == f"Tuya TS110E (ZH-Z06) {BUILD}"                     # no Open Dimmer
    assert f"Module firmware build {BUILD} for the Tuya TS110E (ZH-Z06).\n" in rel["body"]
    assert "Open Dimmer" not in rel["body"]
    # its assets are its own files (the same names, its bytes)
    want = {n: hashlib.sha256((tree / HK_DIR / n).read_bytes()).hexdigest() for n in NAMES + [FORCED]}
    assert {a["name"]: a["digest"] for a in rel["assets"]} == {n: "sha256:" + d for n, d in want.items()}
    after = urls(tree)
    assert after[:2] == [ora.release_url(REPO, TAG, n) for n in NAMES]          # stays the first board's
    assert after[3:] == [ora.release_url(REPO, HK_TAG, n) for n in NAMES]
    assert urls(tree, "index_router-FORCE.json") == [ora.release_url(REPO, TAG, FORCED),
                                                     ora.release_url(REPO, HK_TAG, FORCED)]


def test_two_boards_run_again_change_nothing(tree):
    add_second_board(tree)
    gh = FakeGitHub()
    run_board(tree, gh, BOARD_DIR, "zdms16-2", "AVATTO ZDMS16-2")
    run_board(tree, gh, HK_DIR, "ts110e-3mpwqzuu", "Tuya TS110E (ZH-Z06)", "--carried-image", "")
    texts = [(tree / "zigbee2mqtt/ota" / n).read_text() for n in ("index_router.json", "index_router-FORCE.json")]
    gh.calls.clear()
    # links on the releases now: each run finds its files in its own directory only
    assert run_board(tree, gh, BOARD_DIR, "zdms16-2", "AVATTO ZDMS16-2") == 0
    assert run_board(tree, gh, HK_DIR, "ts110e-3mpwqzuu", "Tuya TS110E (ZH-Z06)", "--carried-image", "") == 0
    assert gh.calls == []
    assert [(tree / "zigbee2mqtt/ota" / n).read_text() for n in ("index_router.json", "index_router-FORCE.json")] == texts


def test_a_board_not_in_the_index_publishes_nothing(tree):
    """build.yml runs the step for each board; a build that left one out has nothing of it."""
    gh = FakeGitHub()
    assert run_board(tree, gh, HK_DIR, "ts110e-3mpwqzuu", "Tuya TS110E (ZH-Z06)", "--carried-image", "") == 0
    assert gh.calls == []


def test_rewrite_matches_by_name_and_sha512():
    text = json.dumps([{"fileName": "a.zigbee", "sha512": "1", "url": "x"},
                       {"fileName": "a.zigbee", "sha512": "2", "url": "y"}], indent=2)
    out = json.loads(ora.rewrite(text, {("a.zigbee", "2"): "z"}))
    assert [e["url"] for e in out] == ["x", "z"]
