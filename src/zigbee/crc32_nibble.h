#ifndef _CRC32_NIBBLE_H_
#define _CRC32_NIBBLE_H_

/* CRC-32 (zlib's polynomial, reflected 0xEDB88320) as the Telink SDK's xcrc32 computes it
 * (proj/common/utility.c): the register is the caller's init in and the result out, neither
 * inverted. The one implementation in the firmware: the SDK's own (a 1 KB table) is renamed away
 * in its utility.o (src/telink/sdk.mk), so its NV and OTA code call this one; zlib's crc32(c, p, n)
 * is ~xcrc32(p, n, ~c). */
unsigned int xcrc32(const unsigned char *buf, int len, unsigned int init);

#endif
