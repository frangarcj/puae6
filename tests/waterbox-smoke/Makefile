TARGET := p6-smoke.wbx
SRCS := guest.cpp
WBX_TARGET_ARCH := aarch64
WBX_HOST_PAGE_SIZE := 16384
OUT_DIR := $(abspath ../../build/waterbox/guest)
override EMULIBC_OBJS = $(OBJ_DIR)/emulibc.c.o
include ../../references/machawk/waterbox/common.mak

# Compile the captured source into our output tree, never into references/MacHawk.
$(OBJ_DIR)/emulibc.c.o: $(WATERBOX_DIR)/emulibc/emulibc.c
	@mkdir -p $(@D)
	$(CC) -c -o $@ $< $(CCFLAGS) $(CCFLAGS_RELEASE)
