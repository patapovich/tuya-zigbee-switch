"""make_ota.py --max-size: a Telink OTA file larger than the device's OTA
client takes is not written. The Telink SDK refuses a QueryNextImageResponse
whose image size (the whole .zigbee file) is over FLASH_OTA_IMAGE_MAX_SIZE,
0x34000 in the 512 KB flash map this firmware uses; src/telink/Makefile reads
that limit from the SDK and passes it for every OTA file."""
import importlib.util
import re
from pathlib import Path

import pytest

click_testing = pytest.importorskip("click.testing")

ROOT = Path(__file__).resolve().parent.parent
SDK_OTA_MAX = 0x34000
# Zigbee OTA header + sub-element header + the CRC make_ota.py appends.
OVERHEAD = 56 + 6 + 4


def make_ota():
    spec = importlib.util.spec_from_file_location(
        "make_ota", ROOT / "src" / "telink" / "make_ota.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(tmp_path, bin_size, max_size):
    src = tmp_path / "fw.bin"
    out = tmp_path / "fw.zigbee"
    src.write_bytes(b"\x00" * bin_size)
    result = click_testing.CliRunner().invoke(
        make_ota().cli,
        [
            "create-ota",
            "--image-type", "0xb810",
            "--file-version", "0x01130300",
            "--max-size", hex(max_size),
            str(src), str(out),
        ],
    )
    return result, out


def test_file_of_exactly_the_limit_is_written(tmp_path):
    # 212912 is a multiple of 16: no padding, the file is the bin + 66 B.
    result, out = run(tmp_path, 212912, 212912 + OVERHEAD)
    assert result.exit_code == 0, result.output
    assert out.stat().st_size == 212912 + OVERHEAD
    assert "0 left" in result.output


def test_padding_and_crc_count_against_the_limit(tmp_path):
    # The raw bin + headers would be exactly 0x34000, but make_ota.py pads the
    # bin to 16 B (2 more) before the CRC: 2 bytes over, nothing written.
    result, out = run(tmp_path, SDK_OTA_MAX - OVERHEAD, SDK_OTA_MAX)
    assert result.exit_code != 0
    assert "212994 bytes, 2 over" in result.output
    assert not out.exists()


def test_old_from_tuya_bound_is_refused(tmp_path):
    # 0x38000 was once taken as the limit; the SDK client takes 16 KB less.
    result, out = run(tmp_path, 0x38000, SDK_OTA_MAX)
    assert result.exit_code != 0
    assert not out.exists()


def test_low_margin_warns(tmp_path):
    # The ZDMS16-2 image of this branch: 211677 B bin -> 211746 B file.
    result, out = run(tmp_path, 211677, SDK_OTA_MAX)
    assert result.exit_code == 0, result.output
    assert out.stat().st_size == 211746
    assert "WARNING" in result.output and "1246 left" in result.output

    result, _ = run(tmp_path, 100000, SDK_OTA_MAX)
    assert result.exit_code == 0 and "WARNING" not in result.output


def test_telink_ota_target_passes_the_sdk_limit():
    makefile = (ROOT / "src" / "telink" / "Makefile").read_text()
    recipe = re.search(r"^ota:.*?(?=^\S)", makefile, re.S | re.M).group(0)
    assert "FLASH_OTA_IMAGE_MAX_SIZE" in makefile
    assert "--max-size $$max_size" in recipe
    # A refused file must fail the build, not just print an error.
    assert re.search(r"\$\(OTA_FILE\) \|\| exit 1", recipe)
