TARGET := p6-smoke.wbx
SRCS := guest.cpp
CXXFLAGS := -femulated-tls
WBX_TARGET_ARCH := aarch64
WBX_HOST_PAGE_SIZE := 16384
OUT_DIR := $(abspath ../../build/waterbox/guest)
override EMULIBC_OBJS = $(OBJ_DIR)/emulibc.c.o $(OBJ_DIR)/emutls.c.o
include ../../references/machawk/waterbox/common.mak

# Compile the captured source into our output tree, never into references/MacHawk.
$(OBJ_DIR)/emulibc.c.o: $(WATERBOX_DIR)/emulibc/emulibc.c
	@mkdir -p $(@D)
	$(CC) -c -o $@ $< $(CCFLAGS) $(CCFLAGS_RELEASE)

# Standard compiler-rt lowering keeps thread_local behind musl's x28 TLS ABI.
$(OBJ_DIR)/emutls.c.o: ../../references/compiler-rt-emutls/compiler-rt/lib/builtins/emutls.c
	@mkdir -p $(@D)
	$(CC) -c -o $@ $< $(CCFLAGS) $(CCFLAGS_RELEASE)
