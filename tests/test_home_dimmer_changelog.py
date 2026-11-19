"""The build changelog of home-dimmer (docs/changelog_home_dimmer.md) and the script py32-bump
stamps it with (helper_scripts/home_dimmer_changelog.py)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "helper_scripts" / "home_dimmer_changelog.py"
CHANGELOG = ROOT / "docs" / "changelog_home_dimmer.md"

spec = importlib.util.spec_from_file_location("home_dimmer_changelog", SCRIPT)
hdc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hdc)

SAMPLE = """# home-dimmer builds

Intro.

## Next

- A new thing,
  wrapped.
- Another.

## 1.1.3-ed1016b5 (2026-11-18), carries Open Dimmer 2.1.12

- Carries Open Dimmer 2.1.12.

## 1.1.3-c28a892e (2026-11-17), carries no Open Dimmer image

- First.
"""


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], capture_output=True,
                          text=True, cwd=cwd)


def test_stamp_turns_next_into_the_build_section_with_the_new_carried_version():
    out = hdc.stamp(SAMPLE, "1.1.3-0a1b2c3d", "2.1.13", "2026-11-19")
    assert "## Next\n\n## 1.1.3-0a1b2c3d (2026-11-19), carries Open Dimmer 2.1.13\n\n" \
           "- Carries Open Dimmer 2.1.13.\n- A new thing,\n  wrapped.\n- Another.\n\n" \
           "## 1.1.3-ed1016b5" in out
    assert out.startswith("# home-dimmer builds\n\nIntro.\n\n## Next\n")
    assert out.count("## Next") == 1
    assert out.endswith("- First.\n")


def test_stamp_of_the_same_version_adds_no_carry_line_and_an_empty_next_says_rebuilt():
    once = hdc.stamp(SAMPLE, "1.1.3-0a1b2c3d", "2.1.13", "2026-11-19")
    again = hdc.stamp(once, "1.1.3-9f8e7d6c", "2.1.13", "2026-11-20")
    assert "## 1.1.3-9f8e7d6c (2026-11-20), carries Open Dimmer 2.1.13\n\n" \
           "- Rebuilt, nothing new; carries Open Dimmer 2.1.13.\n\n## 1.1.3-0a1b2c3d" in again
    same = SAMPLE.replace("- A new thing,\n  wrapped.\n- Another.\n", "- Only this.\n")
    out = hdc.stamp(same, "1.1.3-0a1b2c3d", "2.1.12", "2026-11-19")
    assert "carries Open Dimmer 2.1.12\n\n- Only this.\n\n## 1.1.3-ed1016b5" in out


def test_stamp_is_idempotent_also_for_an_abbreviated_id():
    once = hdc.stamp(SAMPLE, "1.1.3-0a1b2c3d", "2.1.13", "2026-11-19")
    assert hdc.stamp(once, "1.1.3-0a1b2c3d", "2.1.13", "2026-11-19") == once
    assert hdc.stamp(once, "1.1.3-0a1b2c3", "2.1.13", "2026-11-19") == once


@pytest.mark.parametrize("build,carries", [("ed1016b5", "2.1.13"), ("1.1.3-xyz", "2.1.13"),
                                           ("1.1.3-0a1b2c3d", "2.1"), ("1.1.3-0a1b", "2.1.13")])
def test_stamp_refuses_a_bad_build_or_version(build, carries):
    with pytest.raises(ValueError):
        hdc.stamp(SAMPLE, build, carries, "2026-11-19")


def test_stamp_needs_exactly_one_next():
    with pytest.raises(ValueError):
        hdc.stamp(SAMPLE.replace("## Next", "## Later"), "1.1.3-0a1b2c3d", "2.1.13", "2026-11-19")
    with pytest.raises(ValueError):
        hdc.stamp(SAMPLE + "\n## Next\n", "1.1.3-0a1b2c3d", "2.1.13", "2026-11-19")


def test_section_by_full_build_id_or_abbreviation():
    want = "## 1.1.3-ed1016b5 (2026-11-18), carries Open Dimmer 2.1.12\n\n- Carries Open Dimmer 2.1.12."
    assert hdc.section(SAMPLE, "1.1.3-ed1016b5") == want
    assert hdc.section(SAMPLE, "ed1016b5") == want
    assert hdc.section(SAMPLE, "ed1016b") == want
    assert hdc.section(SAMPLE, "1.1.3-ed1016b") == want
    assert hdc.section(SAMPLE, "ed1016") is None          # too short to name a build
    assert hdc.section(SAMPLE, "1.1.4-ed1016b5") is None
    assert hdc.section(SAMPLE, "Next") is None


def test_cli_stamp_and_section(tmp_path):
    f = tmp_path / "c.md"
    f.write_text(SAMPLE, encoding="utf-8")
    r = run("stamp", "--build", "1.1.3-0a1b2c3d", "--carries", "2.1.13", "--date", "2026-11-19", str(f))
    assert r.returncode == 0 and "section written" in r.stdout
    r = run("stamp", "--build", "1.1.3-0a1b2c3d", "--carries", "2.1.13", str(f))
    assert r.returncode == 0 and "already has its section" in r.stdout
    r = run("section", "0a1b2c3", str(f))
    assert r.returncode == 0 and r.stdout.startswith("## 1.1.3-0a1b2c3d (2026-11-19)")
    assert run("section", "deadbeef", str(f)).returncode == 1
    assert run("stamp", "--build", "nope", "--carries", "2.1.13", str(f)).returncode == 2


def test_the_branch_changelog_parses_and_can_be_stamped():
    text = CHANGELOG.read_text(encoding="utf-8")
    head, sections = hdc.split(text)
    assert [h for h, _ in sections].count("## Next") == 1
    for h, body in sections:
        assert h == "## Next" or hdc.HEADING.match(h), h
        assert h == "## Next" or hdc.bullets(body), f"{h} has no bullets"
    out = hdc.stamp(text, "1.1.3-0a1b2c3d", "9.9.9", "2026-12-31")
    assert hdc.section(out, "0a1b2c3d").startswith(
        "## 1.1.3-0a1b2c3d (2026-12-31), carries Open Dimmer 9.9.9\n\n- Carries Open Dimmer 9.9.9.")
