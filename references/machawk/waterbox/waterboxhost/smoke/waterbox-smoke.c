#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

typedef struct {
	size_t sbrk_size;
	size_t sealed_size;
	size_t invis_size;
	size_t plain_size;
	size_t mmap_size;
} MemoryLayoutTemplate;

typedef struct { unsigned char error_message[1024]; } ReturnVoid;
typedef struct { unsigned char error_message[1024]; void *data; } ReturnPtr;
typedef struct { unsigned char error_message[1024]; uintptr_t data; } ReturnUsize;
typedef struct { unsigned char error_message[1024]; uint8_t data; } ReturnU8;

typedef ptrdiff_t (*ReadCallback)(uintptr_t userdata, unsigned char *data, size_t size);
typedef int (*WriteCallback)(uintptr_t userdata, const unsigned char *data, size_t size);

extern void wbx_create_host(const MemoryLayoutTemplate *, const char *, ReadCallback, uintptr_t, ReturnPtr *);
extern void wbx_destroy_host(void *, ReturnVoid *);
extern void wbx_activate_host(void *, ReturnVoid *);
extern void wbx_deactivate_host(void *, ReturnVoid *);
extern void wbx_get_proc_addr(void *, const char *, ReturnUsize *);
extern void wbx_seal(void *, ReturnVoid *);
extern void wbx_save_state(void *, WriteCallback, uintptr_t, ReturnVoid *);
extern void wbx_load_state(void *, ReadCallback, uintptr_t, ReturnVoid *);
extern void wbx_get_page_data(void *, size_t, ReturnU8 *);

enum { GUEST_PAGE_SIZE = 4096 };
static const uintptr_t GUEST_BASE = UINT64_C(0x36f00000000);

typedef struct {
	unsigned char *data;
	size_t size;
	size_t position;
} Buffer;

static ptrdiff_t read_buffer(uintptr_t userdata, unsigned char *data, size_t size) {
	Buffer *buffer = (Buffer *)userdata;
	size_t remaining = buffer->size - buffer->position;
	if (size > remaining) size = remaining;
	memcpy(data, buffer->data + buffer->position, size);
	buffer->position += size;
	return (ptrdiff_t)size;
}

static int write_buffer(uintptr_t userdata, const unsigned char *data, size_t size) {
	Buffer *buffer = (Buffer *)userdata;
	unsigned char *resized = realloc(buffer->data, buffer->size + size);
	if (!resized) return -1;
	buffer->data = resized;
	memcpy(buffer->data + buffer->size, data, size);
	buffer->size += size;
	return 0;
}

static void check(const unsigned char *error_message, const char *operation) {
	if (error_message[0]) {
		fprintf(stderr, "%s: %s\n", operation, error_message);
		exit(1);
	}
}

static Buffer read_file(const char *path) {
	FILE *file = fopen(path, "rb");
	if (!file) {
		perror(path);
		exit(1);
	}
	fseek(file, 0, SEEK_END);
	long length = ftell(file);
	rewind(file);
	Buffer result = { malloc((size_t)length), (size_t)length, 0 };
	if (!result.data || fread(result.data, 1, result.size, file) != result.size) {
		fprintf(stderr, "could not read %s\n", path);
		exit(1);
	}
	fclose(file);
	return result;
}

static uintptr_t get_proc(void *host, const char *name) {
	ReturnUsize result = {0};
	wbx_get_proc_addr(host, name, &result);
	check(result.error_message, name);
	if (!result.data) {
		fprintf(stderr, "missing guest export: %s\n", name);
		exit(1);
	}
	return result.data;
}

static bool page_is_dirty(void *host, size_t index) {
	ReturnU8 result = {0};
	wbx_get_page_data(host, index, &result);
	check(result.error_message, "get page data");
	return (result.data & 0x80) != 0;
}

int main(int argc, char **argv) {
	if (argc != 2) {
		fprintf(stderr, "usage: %s smoke.wbx\n", argv[0]);
		return 2;
	}

	Buffer image = read_file(argv[1]);
	if (image.size < 20 || memcmp(image.data, "\x7f" "ELF", 4) != 0
		|| image.data[18] != 183 || image.data[19] != 0) {
		fprintf(stderr, "%s is not an AArch64 ELF guest\n", argv[1]);
		return 1;
	}

	fflush(stdout);
	int saved_stdout = dup(STDOUT_FILENO);
	int null_stdout = open("/dev/null", O_WRONLY);
	if (saved_stdout == -1 || null_stdout == -1 || dup2(null_stdout, STDOUT_FILENO) == -1) {
		perror("redirect stdout");
		return 1;
	}
	close(null_stdout);

	MemoryLayoutTemplate layout = {
		.sbrk_size = 0x4000,
		.sealed_size = 0x4000,
		.invis_size = 0x4000,
		.plain_size = 0x4000,
		.mmap_size = 0x4000,
	};
	ReturnPtr created = {0};
	wbx_create_host(&layout, "smoke.wbx", read_buffer, (uintptr_t)&image, &created);
	check(created.error_message, "create host");
	void *host = created.data;

	ReturnVoid result = {0};
	wbx_activate_host(host, &result);
	check(result.error_message, "activate host");

	typedef int (*Add)(int, int);
	typedef void (*Increment)(void);
	typedef int (*GetCounter)(void);
	typedef uintptr_t (*GetCounterAddress)(void);
	Add add = (Add)get_proc(host, "add");
	Increment increment = (Increment)get_proc(host, "increment");
	GetCounter get_counter = (GetCounter)get_proc(host, "get_counter");
	GetCounterAddress get_counter_address = (GetCounterAddress)get_proc(host, "get_counter_address");

	int sum = add(2, 3);
	wbx_seal(host, &result);
	check(result.error_message, "seal");

	size_t guest_pages_per_host_page = (size_t)sysconf(_SC_PAGESIZE) / GUEST_PAGE_SIZE;
	size_t counter_page = (size_t)((get_counter_address() - GUEST_BASE) / GUEST_PAGE_SIZE);
	size_t group_start = counter_page - counter_page % guest_pages_per_host_page;
	bool clean_before_write = true;
	for (size_t i = 0; i < guest_pages_per_host_page; i++) {
		clean_before_write &= !page_is_dirty(host, group_start + i);
	}

	increment();
	int before_save = get_counter();
	size_t dirty_after_write = 0;
	for (size_t i = 0; i < guest_pages_per_host_page; i++) {
		dirty_after_write += page_is_dirty(host, group_start + i);
	}
	bool write_fault_caught = clean_before_write && dirty_after_write == guest_pages_per_host_page;

	Buffer state = {0};
	wbx_save_state(host, write_buffer, (uintptr_t)&state, &result);
	check(result.error_message, "save state");

	increment();
	int after_mutation = get_counter();
	state.position = 0;
	wbx_load_state(host, read_buffer, (uintptr_t)&state, &result);
	check(result.error_message, "load state");
	int after_restore = get_counter();
	fflush(stdout);
	if (dup2(saved_stdout, STDOUT_FILENO) == -1) {
		perror("restore stdout");
		return 1;
	}
	close(saved_stdout);

	printf("host page size: %ld\n", sysconf(_SC_PAGESIZE));
	printf("guest page size: %d\n\n", GUEST_PAGE_SIZE);
	printf("clean pages before write: %s\n", clean_before_write ? "yes" : "no");
	printf("write fault caught: %s\n", write_fault_caught ? "yes" : "no");
	printf("dirty pages after write: %zu\n", dirty_after_write);
	printf("counter: %d\n\n", before_save);
	printf("save\n");
	printf("counter = %d\n", after_mutation);
	printf("load\n");
	printf("counter = %d\n\n", after_restore);
	bool passed = sum == 5 && clean_before_write && write_fault_caught
		&& dirty_after_write == 4 && before_save == 1 && after_mutation == 2 && after_restore == 1;
	printf("%s\n", passed ? "PASS" : "FAIL");

	wbx_deactivate_host(host, &result);
	check(result.error_message, "deactivate host");
	wbx_destroy_host(host, &result);
	check(result.error_message, "destroy host");
	free(image.data);
	free(state.data);
	return passed ? 0 : 1;
}
