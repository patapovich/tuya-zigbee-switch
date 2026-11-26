"""board.mk's build id (VERSION_STR: Basic swBuildId and the image file names): the version and
the commit's short hash, and a "+" after it when what goes into the image differs from that
commit, so a unit running a build of a changed tree does not show the commit's id.

Here board.mk runs in a throwaway git repository (make's database only, nothing built), with a
commit of its own files and a change made to each kind of path."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=test",
                           "-c", "user.email=test@example.invalid", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "src" / "zigbee").mkdir(parents=True)
    shutil.copy(ROOT / "board.mk", r / "board.mk")
    (r / "VERSION").write_text("1.1.3\n")
    (r / "NVM_MIGRATIONS_VERSION").write_text("1\n")
    (r / "device_db.yaml").write_text("X:\n  device_type: router\n")
    (r / "src" / "zigbee" / "a.c").write_text("int a;\n")
    (r / ".gitignore").write_text("build\n")
    for d in ("bin/router/X", "zigbee2mqtt/ota"):
        (r / d).mkdir(parents=True)
    (r / "bin/router/X/old.zigbee").write_text("x")
    (r / "zigbee2mqtt/ota/index.json").write_text("[]")
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "c")
    return r


def version_str(repo: Path) -> str:
    out = subprocess.run(["make", "-s", "-f", "board.mk", "-pn", "help", "BOARD=X"], cwd=repo,
                         capture_output=True, text=True, timeout=60).stdout
    m = re.search(r"^VERSION_STR := (.*)$", out, re.M)
    assert m, out[-2000:]
    return m.group(1)


pytestmark = pytest.mark.skipif(GIT is None, reason="no git")


def test_a_clean_tree_is_the_commits_id(repo):
    assert version_str(repo) == "1.1.3-" + git(repo, "rev-parse", "--short", "HEAD")


@pytest.mark.parametrize("change", ["source", "new_source", "board_db", "board_mk", "version",
                                    "nvm_migrations", "staged"])
def test_a_change_to_what_goes_into_the_image_adds_a_plus(repo, change):
    head = git(repo, "rev-parse", "--short", "HEAD")
    if change == "source":
        (repo / "src/zigbee/a.c").write_text("int a = 1;\n")
    elif change == "new_source":
        (repo / "src/zigbee/b.c").write_text("int b;\n")          # not yet added
    elif change == "board_db":
        (repo / "device_db.yaml").write_text("X:\n  device_type: end_device\n")
    elif change == "board_mk":
        (repo / "board.mk").write_text((repo / "board.mk").read_text() + "\n# a change\n")
    elif change == "version":
        (repo / "VERSION").write_text("1.1.4\n")
    elif change == "nvm_migrations":
        (repo / "NVM_MIGRATIONS_VERSION").write_text("2\n")
    else:
        (repo / "src/zigbee/a.c").write_text("int a = 2;\n")
        git(repo, "add", "src/zigbee/a.c")
    got = version_str(repo)
    want_version = "1.1.4" if change == "version" else "1.1.3"
    assert got == f"{want_version}-{head}+"
    assert len(got) <= 16


def test_what_a_build_or_ci_writes_is_no_change(repo):
    """The images and index files a build writes (CI writes them between boards and commits them
    after), and the build directory (ignored)."""
    (repo / "bin/router/X/tlc_switch-1.1.3-x.zigbee").write_text("new")
    (repo / "bin/router/X/old.zigbee").write_text("changed")
    (repo / "zigbee2mqtt/ota/index.json").write_text("[{}]")
    (repo / "build" / "telink").mkdir(parents=True)
    (repo / "build/telink/a.o").write_text("o")
    assert version_str(repo) == "1.1.3-" + git(repo, "rev-parse", "--short", "HEAD")


def test_the_id_is_what_the_image_and_the_file_names_get():
    text = (ROOT / "board.mk").read_text()
    assert re.search(r"^VERSION_STR := \$\(shell echo \"\$\(VERSION_FILE_CONTENT\)-\$\(GIT_HASH\)"
                     r"\$\(if \$\(GIT_DIRTY\),\+\)\" \| cut -c1-16\)$", text, re.M)
    for name in ("BIN_FILE", "OTA_FILE", "FROM_TUYA_OTA_FILE", "FORCE_OTA_FILE"):
        assert re.search(r"^" + name + r" := \$\(BIN_PATH\)/\$\(PROJECT_NAME\)-\$\(VERSION_STR\)",
                         text, re.M), name
    assert "VERSION_STR=$(VERSION_STR)" in text                  # the firmware's swBuildId
