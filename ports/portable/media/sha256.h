#ifndef P6_SHA256_H
#define P6_SHA256_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
enum { P6_SHA256_DIGEST_BYTES = 32, P6_SHA256_BLOCK_BYTES = 64 };

/* FIPS 180-4 SHA-256. Plain C, no libc calls, no allocation, no globals: words are
 * built from bytes big-endian, so the digest is the same on every host. Fields are
 * internal; `used` is always below 64 between calls. `bytes` counts the whole message,
 * and the length field of the padding is that count times 8 modulo 2^64 (FIPS limit). */
typedef struct p6_sha256 {
 uint32_t h[8];
 uint64_t bytes;
 uint8_t block[64];
 uint32_t used;
} p6_sha256;

void p6_sha256_init(p6_sha256 *c);
/* data may be NULL only when bytes == 0. Any split of the message gives the same digest. */
void p6_sha256_update(p6_sha256 *c, const void *data, uint64_t bytes);
/* Writes the digest and wipes the context; call p6_sha256_init before reusing it. */
void p6_sha256_final(p6_sha256 *c, uint8_t out[32]);
/* init + update + final in one call. Not named p6_sha256: in C that identifier is already the typedef. */
void p6_sha256_hash(const void *data, uint64_t bytes, uint8_t out[32]);
#ifdef __cplusplus
}
#endif
#endif
