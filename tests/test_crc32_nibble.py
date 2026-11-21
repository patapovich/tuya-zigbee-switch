"""src/zigbee/crc32_nibble.c, the firmware's one CRC-32 (the SDK's NV and OTA code call it as
xcrc32): the same results as the SDK's 256-entry table version, and zlib's crc32 through the
inversions (what tuya_mcu_ota.c's image CRC does)."""
import ctypes
import os
import random
import subprocess
import zlib

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The SDK's xcrc32 (proj/common/utility.c), its table built the way zlib builds it.
REFERENCE = r"""
static unsigned int table[256];
static void build(void) {
    for (unsigned int n = 0; n < 256; n++) {
        unsigned int c = n;
        for (int k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        table[n] = c;
    }
}
unsigned int xcrc32_table(const unsigned char *buf, int len, unsigned int init) {
    if (!table[1]) build();
    unsigned int crc = init;
    while (len--) { crc = (crc >> 8) ^ table[(crc ^ *buf) & 0xff]; buf++; }
    return crc;
}
"""


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    d = tmp_path_factory.mktemp("crc")
    ref = d / "ref.c"
    ref.write_text(REFERENCE)
    so = d / "crc.so"
    subprocess.run(["cc", "-O1", "-shared", "-fPIC", "-I", os.path.join(ROOT, "src"),
                    os.path.join(ROOT, "src", "zigbee", "crc32_nibble.c"), str(ref), "-o", str(so)],
                   check=True)
    lib = ctypes.CDLL(str(so))
    for f in (lib.xcrc32, lib.xcrc32_table):
        f.restype = ctypes.c_uint
        f.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
    return lib


def test_the_same_as_the_sdk_table_version(lib):
    rnd = random.Random(1)
    for n in (0, 1, 2, 3, 15, 16, 17, 255, 256, 1000, 4096):
        for _ in range(8):
            data = bytes(rnd.randrange(256) for _ in range(n))
            init = rnd.getrandbits(32)
            assert lib.xcrc32(data, n, init) == lib.xcrc32_table(data, n, init)


def test_zlib_crc32_through_the_inversions(lib):
    rnd = random.Random(2)
    for n in (0, 1, 7, 128, 3628):
        data = bytes(rnd.randrange(256) for _ in range(n))
        c = rnd.getrandbits(32)
        assert ~lib.xcrc32(data, n, ~c & 0xFFFFFFFF) & 0xFFFFFFFF == zlib.crc32(data, c)
    assert ~lib.xcrc32(b"123456789", 9, 0xFFFFFFFF) & 0xFFFFFFFF == 0xCBF43926   # the check value


def test_a_negative_length_reads_nothing(lib):
    assert lib.xcrc32(b"abc", -1, 0x12345678) == 0x12345678
