// Deliberate defects, one per mode, to prove that the sanitizer build notices what it is meant to notice. A control
// mode has no defect and must run silently. Never linked into the engine or the bridge.
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

static volatile int sink;
static int global_array[8];

static int heap_overflow()
{
    int *p = new int[8];
    p[int(sink) + 8] = 1;            // one past the end
    int v = p[0];
    delete[] p;
    return v;
}

static int stack_overflow()
{
    int a[8] = {0};
    a[int(sink) + 8] = 1;
    return a[0];
}

static int global_overflow()
{
    global_array[int(sink) + 8] = 1;
    return global_array[0];
}

static int use_after_free()
{
    int *p = new int(3);
    delete p;
    return *p;
}

static int signed_overflow()
{
    int v = INT_MAX;
    v += int(sink) + 1;
    return v;
}

static int negative_shift()
{
    int v = -22;
    return v << (int(sink) + 1);     // the pattern found in drawing.cpp (patch 0061)
}

static int null_pointer_arithmetic()
{
    unsigned *p = nullptr;
    p += int(sink) + 2;              // the other pattern found in drawing.cpp
    return p != nullptr;
}

static int misaligned_access()
{
    char buffer[16] = {0};
    return *reinterpret_cast<int *>(buffer + 1 + int(sink));
}

static int vector_out_of_bounds()
{
    std::vector<int> v(4, 0);
    return v.data()[int(sink) + 4];  // contiguous-container overflow through the raw pointer
}

static int control()
{
    int a[8] = {0};
    a[int(sink) + 7] = 1;
    return a[7] - 1;
}

int main(int argc, char **argv)
{
    static const struct { const char *name; int (*run)(); } modes[] = {
        {"heap-overflow", heap_overflow}, {"stack-overflow", stack_overflow}, {"global-overflow", global_overflow},
        {"use-after-free", use_after_free}, {"signed-overflow", signed_overflow}, {"negative-shift", negative_shift},
        {"null-pointer-arithmetic", null_pointer_arithmetic}, {"misaligned-access", misaligned_access},
        {"vector-out-of-bounds", vector_out_of_bounds}, {"control", control},
    };
    if (argc != 2) return 64;
    for (const auto &mode : modes)
        if (!strcmp(mode.name, argv[1])) return mode.run() == 12345 ? 1 : 0;
    return 64;
}
