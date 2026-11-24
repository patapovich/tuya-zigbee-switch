"""helper_scripts/make_z2m_ota_index.py: an image of one board is also offered to the stock
names of the boards with the same config_str (the same image), as CI builds one ZDMS16-2
board for all three."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "helper_scripts" / "make_z2m_ota_index.py"


def ota_file(path: Path) -> Path:
    head = bytearray(64)
    head[10:12] = (4417).to_bytes(2, "little")      # manufacturer code
    head[12:14] = (54179).to_bytes(2, "little")     # image type
    head[14:18] = (0xFFFFFFFF).to_bytes(4, "little")
    head[20:52] = b"x" * 32
    path.write_bytes(bytes(head))
    return path


def index_names(tmp_path: Path, board: str, db_file: Path) -> list[str]:
    index = tmp_path / f"index-{board}.json"
    subprocess.run([sys.executable, str(SCRIPT), "--db_file", str(db_file), str(ota_file(tmp_path / "a.zigbee")),
                    str(index), "--board", board, "--base-url", "https://example.invalid"], check=True)
    (entry,) = json.loads(index.read_text())
    return entry["manufacturerName"]


def test_the_zdms16_2_conversion_is_offered_to_all_three_stock_names(tmp_path):
    names = index_names(tmp_path, "MODULE_AVATTO_ZDMS16_2", ROOT / "device_db.yaml")
    assert names == ["_TZE284_jtbgusdc", "_TZE204_jtbgusdc", "_TZE204_o9gyszw2", "jtbgusdc"]


def test_a_board_without_a_twin_keeps_its_own_names(tmp_path):
    db = tmp_path / "db.yaml"
    db.write_text("A:\n  config_str: a;X;\n  stock_manufacturer_name: _TZ_a\n"
                  "B:\n  config_str: b;Y;\n  stock_manufacturer_name: _TZ_b\n"
                  "C:\n  config_str: a;X;\n  stock_manufacturer_name: _TZ_a\n")
    assert index_names(tmp_path, "A", db) == ["_TZ_a", "a"]
    assert index_names(tmp_path, "B", db) == ["_TZ_b", "b"]
