// Full pinned CPU descriptor parser. HOST and P6 builds emit comparable rows.
#include "sysconfig.h"
#include "sysdeps.h"
#include "readcpu.h"

#include <assert.h>
#include <stdio.h>
#include <string>

static void check_decoding()
{
	assert(table68k[0x4e71].mnemo == i_NOP); // NOP
	assert(table68k[0x70ff].mnemo == i_MOVE); // MOVEQ #$ff,D0
	assert(table68k[0x70ff].size == sz_long);
	assert(table68k[0x70ff].smode == immi && table68k[0x70ff].dmode == Dreg);
	assert(table68k[0x70ff].dreg == 0 && table68k[0x70ff].sreg == 255);
	assert(table68k[0x70ff].handler == 0x7000); // merged register/immediate variants
	assert(table68k[0x7eff].mnemo == i_MOVE && table68k[0x7eff].dreg == 7);
	assert(table68k[0x7eff].handler == 0x7000);
	assert(table68k[0x6600].mnemo == i_Bcc); // BNE.W
	assert(table68k[0x6600].cc == 6 && table68k[0x6600].size == sz_word);
	assert(table68k[0x6600].smode == imm1);
	assert(table68k[0x6601].mnemo == i_Bcc); // BNE.B
	assert(table68k[0x6601].cc == 6 && table68k[0x6601].size == sz_byte);
}

static void append_row(std::string &out, unsigned opcode, const instr &row)
{
	char line[512];
	int n;
	if (row.mnemo == i_ILLG) {
		// The parser initializes only these two fields in illegal slots.
		n = snprintf(line, sizeof line, "%04x,%u,%ld\n", opcode,
			(unsigned)row.mnemo, row.handler);
	} else {
		// `unused2` and structure padding are intentionally omitted: upstream
		// does not initialize them. Keep every semantic field that it does set.
		n = snprintf(line, sizeof line,
			"%04x,%u,%ld,%u,%u,%d,%d,%u,%d,%d,%d,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%d,%d,%d,%d,%d\n",
			opcode, (unsigned)row.mnemo, row.handler,
			(unsigned)row.dreg, (unsigned)row.sreg, (int)row.dpos, (int)row.spos,
			(unsigned)row.sduse, (int)row.flagdead, (int)row.flaglive,
			(int)row.cc, (unsigned)row.plev, (unsigned)row.size,
			(unsigned)row.unsized, (unsigned)row.smode, (unsigned)row.stype,
			(unsigned)row.dmode, (unsigned)row.suse, (unsigned)row.duse,
			(unsigned)row.ccuse, (unsigned)row.clev, (unsigned)row.unimpclev,
			(int)row.cflow, (int)row.head, (int)row.tail, (int)row.clocks,
			(int)row.fetchmode);
	}
	assert(n > 0 && (size_t)n < sizeof line);
	out.append(line, (size_t)n);
}

static std::string serialize_table()
{
	std::string out;
	out.reserve(8 * 1024 * 1024);
	for (unsigned opcode = 0; opcode < 65536; ++opcode)
		append_row(out, opcode, table68k[opcode]);
	return out;
}

int main()
{
	assert(table68k == NULL);
	init_table68k();
	assert(n_defs68k > 0 && nr_cpuop_funcs > 0);
	check_decoding();
	const int mismatches = get_no_mismatches();
	const int functions = nr_cpuop_funcs;
	const std::string first = serialize_table();

	// Reinitialization must free/rebuild the table deterministically.
	init_table68k();
	assert(nr_cpuop_funcs == functions && get_no_mismatches() == mismatches);
	check_decoding();
	assert(serialize_table() == first);
	exit_table68k();
	assert(table68k == NULL);

	// A clean init after exit must produce the same complete semantic table.
	init_table68k();
	assert(nr_cpuop_funcs == functions && get_no_mismatches() == mismatches);
	check_decoding();
	assert(serialize_table() == first);
	if (printf("defs=%d;functions=%d;mismatches=%d\n", n_defs68k,
			nr_cpuop_funcs, get_no_mismatches()) < 0)
		abort();
	if (fwrite(first.data(), 1, first.size(), stdout) != first.size())
		abort();
	exit_table68k();
	assert(table68k == NULL);
	return 0;
}
