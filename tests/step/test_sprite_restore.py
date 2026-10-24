"""Real sprite serializer arithmetic in isolation; this is not an emulator.

T66r7: the native Brian cut exposed sign extension while restoring a 64-bit
sprite. Compile the actual producer/reader, including 32-bit mode, with high
bits in every data word. Gameplay restoration exercises the complete engine.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.materialize_upstream import materialize

ROOT = Path(__file__).resolve().parents[2]


class SpriteRestoreTests(unittest.TestCase):
    def test_real_sprite_reader_preserves_unsigned_words_in_32_and_64_bit_modes(self):
        materialize(ROOT)
        text = (ROOT / 'build/upstream/drawing.cpp').read_text()
        body = text[text.index('uae_u8 *save_custom_sprite_denise('):text.index('\nstatic void updatelinedata(void)')]
        source = r'''#include <cassert>
#include <cstdint>
#include <cstring>
#include <iostream>
using uae_u8 = uint8_t; using uae_u16 = uint16_t;
using uae_u32 = uint32_t; using uae_u64 = uint64_t;
struct denise_spr { uint64_t dataa64, datab64; uint32_t dataa, datab; int armed, fmode; };
static denise_spr dspr[8];
static bool denise_sprfmode64;
static void sprwrite(int, uae_u16) {} // Register side effects are outside this arithmetic fixture.
static void spr_arm(denise_spr *s, int value) { s->armed = value; }
static uint16_t read16(uint8_t *&p) { const uint16_t v = uint16_t(p[0]) << 8 | p[1]; p += 2; return v; }
static void write16(uint8_t *&p, uint16_t v) { *p++ = uint8_t(v >> 8); *p++ = uint8_t(v); }
#define RW read16(src)
#define RB (*src++)
#define SW(v) write16(dst, uint16_t(v))
#define SB(v) (*dst++ = uint8_t(v))
''' + body + r'''
int main() {
    int wide;
    while (std::cin >> wide) {
        uint16_t words[8]; uint8_t input[17]{}, output[17]{}; auto *p = input;
        for (auto &word : words) { unsigned value; std::cin >> value; word = uint16_t(value); write16(p, word); }
        *p = 7; denise_sprfmode64 = wide; dspr[0] = {};
        assert(restore_custom_sprite_denise(0, input, 0, 0) == input + sizeof input);
        uint64_t a = uint64_t(words[0]) << 16 | words[2], b = uint64_t(words[1]) << 16 | words[3];
        if (wide) { a = a << 32 | uint64_t(words[4]) << 16 | words[6]; b = b << 32 | uint64_t(words[5]) << 16 | words[7]; }
        assert(dspr[0].dataa == uint32_t(words[0]) << 16 && dspr[0].datab == uint32_t(words[1]) << 16);
        assert(dspr[0].dataa64 == a && dspr[0].datab64 == b);
        assert(dspr[0].armed == 1 && dspr[0].fmode == 3);
        assert(save_custom_sprite_denise(0, output) == output + sizeof output);
        assert(!memcmp(input, output, sizeof input));
    }
}
'''
        cases = []
        for wide in (0, 1):
            for lane in range(8 if wide else 2):
                for value in (0, 1, 0x7fff, 0x8000, 0xa55a, 0xffff):
                    words = [0x123, 0x4567, 0x2345, 0x6789, 0x3456, 0x789a, 0x4567, 0x89ab] if wide else [0x123, 0x4567] + [0] * 6
                    words[lane] = value
                    cases.append(' '.join(map(str, (wide, *words))))
        with tempfile.TemporaryDirectory(prefix='sprite-restore-', dir=ROOT / 'build') as tmp:
            directory = Path(tmp); cpp = directory / 'probe.cpp'; exe = directory / 'probe'
            cpp.write_text(source)
            compiled = subprocess.run(['clang++', '-std=c++17', '-O1', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                       str(cpp), '-o', str(exe)], capture_output=True, text=True, timeout=90)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            ran = subprocess.run([str(exe)], input='\n'.join(cases), capture_output=True, text=True, timeout=30,
                                 env={**os.environ, 'ASAN_OPTIONS': 'detect_leaks=0'})
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == '__main__':
    unittest.main()
