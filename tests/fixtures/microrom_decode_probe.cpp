// Walks a raw 68000 code blob with the engine's own opcode decoder (build/upstream/readcpu.cpp plus the
// generated build/generated/cpudefs.cpp) and prints what the decoder says about every instruction:
//
//   OFFSET MNEMONIC SIZE WORDS SMODE DMODE SREG DREG CLEV PLEV
//
// OFFSET is the byte offset in the blob (decimal), SIZE is B/W/L, WORDS counts the opcode word plus the
// extension words implied by the source and destination addressing modes, SREG/DREG are the register or
// immediate fields the decoder extracts, CLEV is the CPU level the opcode needs (0 = 68000) and PLEV is 2 for
// privileged instructions. The exit status is 1 on an illegal opcode or a truncated instruction.
#include "sysconfig.h"
#include "sysdeps.h"
#include "readcpu.h"

#include <stdio.h>
#include <vector>

static const char *const kModes[] = {
	"Dreg", "Areg", "Aind", "Aipi", "Apdi", "Ad16", "Ad8r", "absw", "absl",
	"PC16", "PC8r", "imm", "imm0", "imm1", "imm2", "immi", "unknown", "illegal"};

static const char *mnemonic_name(const instr &row)
{
	if (row.mnemo == i_Bcc && row.cc == 0)
		return "BRA";
	for (int i = 0; lookuptab[i].name[0]; ++i) {
		if ((unsigned)lookuptab[i].mnemo == (unsigned)row.mnemo)
			return lookuptab[i].friendlyname ? lookuptab[i].friendlyname : lookuptab[i].name;
	}
	return "?";
}

// Extension words a source or destination mode adds after the opcode word (68000 formats).
static int extension_words(int mode, int size)
{
	switch (mode) {
	case Ad16: case Ad8r: case absw: case PC16: case PC8r: case imm0: case imm1:
		return 1;
	case absl: case imm2:
		return 2;
	case imm:
		return size == sz_long ? 2 : 1;
	default: // Dreg, Areg, (An), (An)+, -(An), immediate inside the opcode, no operand
		return 0;
	}
}

static bool read_file(const char *path, std::vector<unsigned char> &out)
{
	FILE *file = fopen(path, "rb");
	if (!file)
		return false;
	unsigned char buffer[4096];
	size_t count;
	while ((count = fread(buffer, 1, sizeof buffer, file)) > 0)
		out.insert(out.end(), buffer, buffer + count);
	const bool ok = !ferror(file);
	fclose(file);
	return ok;
}

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: %s CODE.bin\n", argv[0]);
		return 2;
	}
	std::vector<unsigned char> code;
	if (!read_file(argv[1], code)) {
		fprintf(stderr, "cannot read %s\n", argv[1]);
		return 2;
	}
	init_table68k();
	int status = 0;
	size_t at = 0;
	while (at < code.size()) {
		if (at + 2 > code.size()) {
			fprintf(stderr, "truncated opcode at %zu\n", at);
			status = 1;
			break;
		}
		const unsigned opcode = (unsigned)code[at] << 8 | code[at + 1];
		const instr &row = table68k[opcode];
		if (row.mnemo == i_ILLG) {
			fprintf(stderr, "illegal opcode %04x at %zu\n", opcode, at);
			status = 1;
			break;
		}
		const int words = 1 + extension_words(row.smode, row.size) + extension_words(row.dmode, row.size);
		if (at + 2 * (size_t)words > code.size()) {
			fprintf(stderr, "truncated instruction %04x at %zu\n", opcode, at);
			status = 1;
			break;
		}
		const char size = row.size == sz_byte ? 'B' : row.size == sz_word ? 'W' : row.size == sz_long ? 'L' : '?';
		printf("%zu %s %c %d %s %s %d %d %d %d\n", at, mnemonic_name(row), size, words,
			kModes[row.smode], kModes[row.dmode], (int)row.sreg, (int)row.dreg,
			(int)row.clev, (int)row.plev);
		at += 2 * (size_t)words;
	}
	exit_table68k();
	return status;
}
