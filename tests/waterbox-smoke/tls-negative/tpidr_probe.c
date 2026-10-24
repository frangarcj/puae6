// SPDX-License-Identifier: MIT
// Native macOS ARM64 probe, not a guest: shows that XNU overwrites TPIDR_EL0.
// Timing dependent, so it is a manual reproducer for docs/adr/waterbox-compiler-tls.md
// and not part of any automated check:
//   clang -O1 -o tpidr_probe tests/waterbox-smoke/tls-negative/tpidr_probe.c && ./tpidr_probe
// Expected on Apple Silicon: the marker never survives (mismatches=200) and the
// register holds a small value (0x1002..0x1004) that also shows up in a new thread.
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

static uint64_t read_tpidr(void) { uint64_t v; __asm__ volatile("mrs %0, tpidr_el0" : "=r"(v)); return v; }
static void write_tpidr(uint64_t v) { __asm__ volatile("msr tpidr_el0, %0" :: "r"(v)); }
static void *spin(void *arg) { (void)arg; volatile uint64_t x = 0; for (uint64_t i = 0; i < 400000000ull; ++i) x += i; return 0; }
static void *report(void *arg) { (void)arg; printf("new thread initial tpidr=0x%llx\n", (unsigned long long)read_tpidr()); return 0; }

int main(void)
{
	const uint64_t marker = 0x1122334455667788ull;
	printf("initial tpidr=0x%llx\n", (unsigned long long)read_tpidr());
	pthread_t spinners[8];
	for (int i = 0; i < 8; ++i) pthread_create(&spinners[i], 0, spin, 0);
	write_tpidr(marker);
	int mismatches = 0;
	for (int i = 0; i < 200; ++i) {
		usleep(5000);
		sched_yield();
		if (read_tpidr() != marker) ++mismatches;
	}
	printf("after ~1s with 8 spinners: tpidr=0x%llx mismatches=%d\n", (unsigned long long)read_tpidr(), mismatches);
	pthread_t fresh;
	pthread_create(&fresh, 0, report, 0);
	pthread_join(fresh, 0);
	for (int i = 0; i < 8; ++i) pthread_join(spinners[i], 0);
	return mismatches ? 1 : 0;
}
