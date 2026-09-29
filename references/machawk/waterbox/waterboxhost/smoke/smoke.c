#define ECL_EXPORT __attribute__((visibility("default")))

#include <stdint.h>

static int counter;

ECL_EXPORT int add(int a, int b) {
	return a + b;
}

ECL_EXPORT void increment(void) {
	counter++;
}

ECL_EXPORT int get_counter(void) {
	return counter;
}

ECL_EXPORT uintptr_t get_counter_address(void) {
	return (uintptr_t)&counter;
}
