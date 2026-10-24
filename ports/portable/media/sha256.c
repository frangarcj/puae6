#include "sha256.h"

/* No libc on purpose (not even memcpy/memset): this file is hashed by the same code in
 * every build, host and Waterbox, so it carries no dependency that could differ. */

static const uint32_t k[64] = {
 0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
 0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
 0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
 0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
 0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
 0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u
};

static uint32_t rotr(uint32_t x, unsigned n) /* n in 1..31 */
{
 return (x >> n) | (x << (32u - n));
}

/* One 64-byte block, read as sixteen big-endian words. */
static void compress(uint32_t h[8], const uint8_t *p)
{
 uint32_t w[64];
 for (unsigned i = 0; i < 16; ++i)
  w[i] = ((uint32_t)p[4*i] << 24) | ((uint32_t)p[4*i + 1] << 16) | ((uint32_t)p[4*i + 2] << 8) | (uint32_t)p[4*i + 3];
 for (unsigned i = 16; i < 64; ++i) {
  uint32_t s0 = rotr(w[i-15], 7) ^ rotr(w[i-15], 18) ^ (w[i-15] >> 3);
  uint32_t s1 = rotr(w[i-2], 17) ^ rotr(w[i-2], 19) ^ (w[i-2] >> 10);
  w[i] = w[i-16] + s0 + w[i-7] + s1;
 }
 uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
 for (unsigned i = 0; i < 64; ++i) {
  uint32_t t1 = hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + k[i] + w[i];
  uint32_t t2 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
  hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
 }
 h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
}

void p6_sha256_init(p6_sha256 *c)
{
 static const uint32_t iv[8] = {
  0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au, 0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u
 };
 for (unsigned i = 0; i < 8; ++i) c->h[i] = iv[i];
 for (unsigned i = 0; i < 64; ++i) c->block[i] = 0;
 c->bytes = 0; c->used = 0;
}

void p6_sha256_update(p6_sha256 *c, const void *data, uint64_t bytes)
{
 const uint8_t *p = (const uint8_t *)data;
 if (!bytes) return; /* the only case where data may be NULL */
 c->bytes += bytes;
 if (c->used) {
  while (bytes && c->used < 64) { c->block[c->used++] = *p++; --bytes; }
  if (c->used < 64) return;
  compress(c->h, c->block); c->used = 0;
 }
 while (bytes >= 64) { compress(c->h, p); p += 64; bytes -= 64; }
 while (bytes) { c->block[c->used++] = *p++; --bytes; }
}

void p6_sha256_final(p6_sha256 *c, uint8_t out[32])
{
 uint64_t bits = c->bytes << 3;
 c->block[c->used++] = 0x80; /* used < 64 on entry, so this fits */
 if (c->used > 56) { /* no room left for the length: it goes in an extra block */
  while (c->used < 64) c->block[c->used++] = 0;
  compress(c->h, c->block); c->used = 0;
 }
 while (c->used < 56) c->block[c->used++] = 0;
 for (unsigned i = 0; i < 8; ++i) c->block[56 + i] = (uint8_t)(bits >> (56u - 8u*i));
 compress(c->h, c->block);
 for (unsigned i = 0; i < 8; ++i)
  for (unsigned j = 0; j < 4; ++j) out[4*i + j] = (uint8_t)(c->h[i] >> (24u - 8u*j));
 for (unsigned i = 0; i < 8; ++i) c->h[i] = 0;
 for (unsigned i = 0; i < 64; ++i) c->block[i] = 0;
 c->bytes = 0; c->used = 0;
}

void p6_sha256_hash(const void *data, uint64_t bytes, uint8_t out[32])
{
 p6_sha256 c;
 p6_sha256_init(&c);
 p6_sha256_update(&c, data, bytes);
 p6_sha256_final(&c, out);
}
