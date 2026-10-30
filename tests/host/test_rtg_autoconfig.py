"""The bounded RTG board uses the real UAE Z3 Autoconfig path."""
from pathlib import Path
import os
import subprocess
import tempfile
import textwrap
import unittest

from tests.host import ensure_generated


ROOT = Path(__file__).resolve().parents[2]
SANITIZERS = ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
              "-fno-omit-frame-pointer"]

PROBE = r"""
#include "expansion.cpp"

#include <cassert>
#include <cstdint>
#include <cstring>

namespace {
int map_calls;
int map_first;
int map_count;
addrbank *mapped_bank;
}

uae_prefs currprefs{}, changed_prefs{};
addrbank dummy_bank{};
addrbank *mem_banks[MEMORY_BANKS]{};
regstruct regs{};

uae_u32 REGPARAM3 dummy_lgeti(uaecptr) { return 0; }
uae_u32 REGPARAM3 dummy_wgeti(uaecptr) { return 0; }
int REGPARAM3 default_check(uaecptr, uae_u32) { return 0; }
uae_u8 *REGPARAM3 default_xlate(uaecptr) { return nullptr; }
uae_u32 memory_get_long(uaecptr) { return 0; }
uae_u32 memory_get_word(uaecptr) { return 0; }
int memory_valid_address(uaecptr, uae_u32) { return 0; }
void memory_put_long(uaecptr, uae_u32) {}
void memory_put_word(uaecptr, uae_u32) {}
bool cdtv_init(autoconfig_info *) { return false; }
void write_log(const TCHAR *, ...) {}

void map_banks(addrbank *bank, int first, int count, int)
{
	for (int i = 0; i < count; ++i) mem_banks[first + i] = bank;
	if (bank == &dummy_bank) {
		if (mapped_bank && first == int(mapped_bank->start >> 16)) {
			mapped_bank->flags &= ~ABFLAG_MAPPED;
			mapped_bank = nullptr;
		}
		map_first = first;
		map_count = count;
		return;
	}
	mapped_bank = bank;
	map_first = first;
	map_count = count;
	bank->flags |= ABFLAG_MAPPED;
}

void map_banks_z2(addrbank *bank, int first, int count)
{
	map_banks(bank, first, count, 0);
}

void map_banks_z3(addrbank *bank, int first, int count)
{
	++map_calls;
	map_banks(bank, first, count, 0);
}

int main()
{
	static_assert(P6_RTG_APERTURE_BYTES == 0x01000000, "Z3 physical aperture");
	static_assert(P6_RTG_VRAM_BYTES == 0x00400000, "populated subsize");
	for (addrbank *&bank : mem_banks) bank = &dummy_bank;

	static uae_prefs prefs{};
	/* Extra configured slots must never produce another portable board. */
	prefs.rtgboards[1].rtgmem_size = P6_RTG_VRAM_BYTES;
	prefs.rtgboards[1].rtgmem_type = GFXBOARD_UAE_Z3;
	cardno = 0;
	std::memset(cards_set, 0, sizeof cards_set);
	assert(!p6_add_gfxcard_z3(&prefs));
	assert(cardno == 0);

	prefs.rtgboards[0].rtgmem_size = P6_RTG_VRAM_BYTES;
	prefs.rtgboards[0].rtgmem_type = GFXBOARD_UAE_Z3;
	currprefs = prefs;
	assert(p6_add_gfxcard_z3(&prefs));
	assert(cardno == 1);
	assert(cards_set[0].flags == (4 | CARD_FLAG_CAN_Z3));
	assert(cards_set[0].zorro == 3);
	assert(cards_set[0].initnum == expamem_init_gfxcard_z3);
	assert(cards_set[0].map == expamem_map_gfxcard_z3);
	assert(std::strcmp(cards_set[0].name, "Z3RTG") == 0);

	assert(p6_rtg_memory_enable() == P6_RTG_OK);
	uae_id = commodore;
	autoconfig_info aci{};
	aci.prefs = &prefs;
	aci.devnum = 0;
	aci.doinit = true;
	assert(cards_set[0].initnum(&aci));
	assert(aci.addrbank == &gfxmem_bank && aci.direct_vram);
	assert(autoconfig_read(aci.autoconfig_raw, 0x00) == 0x80);
	assert(autoconfig_read(aci.autoconfig_raw, 0x04) == 96);
	assert(autoconfig_read(aci.autoconfig_raw, 0x08) == 0x98);
	assert(autoconfig_read(aci.autoconfig_raw, 0x10) == (commodore >> 8));
	assert(autoconfig_read(aci.autoconfig_raw, 0x14) == (commodore & 0xff));
	assert(autoconfig_read(aci.autoconfig_raw, 0x18) == 0);
	assert(autoconfig_read(aci.autoconfig_raw, 0x1c) == 0);
	assert(autoconfig_read(aci.autoconfig_raw, 0x20) == 0);
	assert(autoconfig_read(aci.autoconfig_raw, 0x24) == 1);

	constexpr uint32_t base = 0x22000000;
	expamem_board_pointer = base;
	assert(cards_set[0].map(&aci) == &gfxmem_bank);
	assert(map_calls == 1 && mapped_bank == &gfxmem_bank);
	assert(map_first == int(base >> 16));
	assert(map_count == int(P6_RTG_VRAM_BYTES >> 16));
	assert(uint32_t(map_count) * 0x10000u == P6_RTG_VRAM_BYTES);
	assert(uint32_t(map_count) * 0x10000u < P6_RTG_APERTURE_BYTES);
	assert(gfxmem_bank.start == base);
	gfxmem_bank.bput(base, 0x12);
	gfxmem_bank.bput(base + P6_RTG_VRAM_BYTES - 1, 0x34);
	assert(gfxmem_bank.bget(base) == 0x12);
	assert(gfxmem_bank.bget(base + P6_RTG_VRAM_BYTES - 1) == 0x34);

	addrbank before = gfxmem_bank;
	expamem_board_pointer = base + 0x10000;
	assert(cards_set[0].map(&aci) == nullptr);
	assert(map_calls == 1);
	assert(std::memcmp(&before, &gfxmem_bank, sizeof before) == 0);

	/* The semantic expansion tail restores RTG mapping without touching VRAM. */
	ecard = cardno;
	expamem_map = nullptr;
	expamem_bank_current = nullptr;
	expamem_autoconfig_mode = 0;
	uint32_t words[16]{};
	uint8_t autoconfig[256]{};
	assert(p6_expansion_state_get(words, 16, autoconfig));
	assert(words[13] == base && words[14] == P6_RTG_VRAM_BYTES && words[15] == 1);
	const uint8_t first = gfxmem_bank.bget(base);
	const uint8_t last = gfxmem_bank.bget(base + P6_RTG_VRAM_BYTES - 1);

	constexpr uint32_t other_base = 0x24000000;
	assert(p6_rtg_memory_set_bus_base(other_base) == P6_RTG_OK);
	gfxmem_bank.flags |= ABFLAG_MAPPED;
	assert(p6_expansion_state_set(words, 16, autoconfig));
	assert(gfxmem_bank.start == base && (gfxmem_bank.flags & ABFLAG_MAPPED));
	assert(map_calls == 2 && mapped_bank == &gfxmem_bank);
	assert(gfxmem_bank.bget(base) == first);
	assert(gfxmem_bank.bget(base + P6_RTG_VRAM_BYTES - 1) == last);

	auto refused = [&](unsigned index, uint32_t value) {
		uint32_t bad[16];
		std::memcpy(bad, words, sizeof bad);
		bad[index] = value;
		const addrbank stable = gfxmem_bank;
		const int calls = map_calls;
		assert(!p6_expansion_state_set(bad, 16, autoconfig));
		assert(std::memcmp(&stable, &gfxmem_bank, sizeof stable) == 0);
		assert(map_calls == calls && mapped_bank == &gfxmem_bank);
	};
	refused(14, P6_RTG_VRAM_BYTES / 2);
	refused(15, 2);
	refused(13, base + 0x10000);
	refused(15, 0);
	uint32_t absent[16];
	std::memcpy(absent, words, sizeof absent);
	absent[13] = 0;
	assert(!p6_expansion_state_set(absent, 16, autoconfig));
	assert(!p6_expansion_state_set(words, 15, autoconfig));

	/* RTG0 preserves the previous 13-word layout byte for byte. */
	currprefs.rtgboards[0].rtgmem_size = 0;
	uint32_t legacy[13]{};
	assert(p6_expansion_state_get(legacy, 13, autoconfig));
	assert(!p6_expansion_state_get(words, 16, autoconfig));
	currprefs.rtgboards[0].rtgmem_size = P6_RTG_VRAM_BYTES;

	uint8_t *storage = gfxmem_bank.baseaddr;
	p6_rtg_reset();
	assert(gfxmem_bank.baseaddr == storage && gfxmem_bank.start == base);
	assert(gfxmem_bank.bget(base) == 0 && gfxmem_bank.bget(base + P6_RTG_VRAM_BYTES - 1) == 0);
	return 0;
}
"""


class RtgAutoconfigTests(unittest.TestCase):
    def test_z3_identity_single_registration_and_mapping(self):
        ensure_generated()
        tree = ROOT / "build/upstream"
        includes = ["-I" + str(ROOT / "ports/portable"),
                    "-I" + str(ROOT / "build/generated"),
                    "-I" + str(tree / "include"), "-I" + str(tree)]
        with tempfile.TemporaryDirectory(prefix="p6-rtg-autoconfig-") as directory:
            temporary = Path(directory)
            probe = temporary / "probe.cpp"
            probe.write_text(textwrap.dedent(PROBE), encoding="utf-8")
            executable = temporary / "probe"
            subprocess.run([
                "clang++", "-std=c++17", "-UNDEBUG", "-fno-fast-math", "-O1",
                "-ffunction-sections", "-fdata-sections", "-DP6_PORTABLE_RTG=1",
                *SANITIZERS, *includes, str(probe),
                str(ROOT / "ports/portable/rtg_device.cpp"),
                str(ROOT / "ports/portable/rtg_memory.cpp"),
                "-Wl,-dead_strip", "-o", str(executable),
            ], check=True, timeout=180, capture_output=True)
            result = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=120,
                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"})
            self.assertEqual(result.returncode, 0, result.stderr[-1600:])

    def test_portable_branch_never_allocates_or_enables_z2(self):
        ensure_generated()
        source = (ROOT / "build/upstream/expansion.cpp").read_text(encoding="utf-8")
        allocation = source.split("#elif defined(P6_PORTABLE_RTG)", 1)[1].split("#endif", 1)[0]
        self.assertIn("p6_rtg_memory_enable()", allocation)
        self.assertIn("p6_rtg_memory_disable()", allocation)
        self.assertNotIn("mapped_malloc", allocation)
        z2 = source.split("static addrbank *expamem_map_gfxcard_z2", 1)[0]
        self.assertTrue(z2.rstrip().endswith("#ifdef PICASSO96"))
        registration = source.split("static bool p6_add_gfxcard_z3", 1)[1].split("#endif", 1)[0]
        self.assertNotIn("for (", registration)
        self.assertIn("rtgboards[0]", registration)
        clear = source.split("void expansion_clear (void)", 1)[1].split("/* Native state", 1)[0]
        self.assertIn("p6_rtg_reset()", clear)


if __name__ == "__main__":
    unittest.main()
