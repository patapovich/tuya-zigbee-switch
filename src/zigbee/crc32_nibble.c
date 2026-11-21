#include "zigbee/crc32_nibble.h"

/* A 16-entry table, two lookups a byte: the same results as the SDK's 256-entry one in 64 bytes
 * instead of 1024 (tests/test_crc32_nibble.py compares them). */
static const unsigned int k_crc_nibble[16] = {
    0x00000000u, 0x1DB71064u, 0x3B6E20C8u, 0x26D930ACu, 0x76DC4190u, 0x6B6B51F4u,
    0x4DB26158u, 0x5005713Cu, 0xEDB88320u, 0xF00F9344u, 0xD6D6A3E8u, 0xCB61B38Cu,
    0x9B64C2B0u, 0x86D3D2D4u, 0xA00AE278u, 0xBDBDF21Cu,
};

unsigned int xcrc32(const unsigned char *buf, int len, unsigned int init) {
    unsigned int crc = init;

    while (len-- > 0) {
        crc ^= *buf++;
        crc  = (crc >> 4) ^ k_crc_nibble[crc & 15u];
        crc  = (crc >> 4) ^ k_crc_nibble[crc & 15u];
    }
    return crc;
}
