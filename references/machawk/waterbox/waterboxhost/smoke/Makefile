CLANG ?= clang
CARGO ?= cargo
TARGET_DIR := ../target/debug

.PHONY: all clean force run

all: $(TARGET_DIR)/libwaterboxhost.dylib $(TARGET_DIR)/smoke.wbx $(TARGET_DIR)/waterbox-smoke

$(TARGET_DIR):
	mkdir -p $@

$(TARGET_DIR)/libwaterboxhost.dylib: force
	cd .. && RUSTC_BOOTSTRAP=1 $(CARGO) build

force:

$(TARGET_DIR)/smoke.wbx: smoke.c linkscript_aarch64.ld build-guest.sh | $(TARGET_DIR)
	sh ./build-guest.sh

$(TARGET_DIR)/waterbox-smoke: waterbox-smoke.c $(TARGET_DIR)/libwaterboxhost.dylib
	$(CLANG) -arch arm64 -Wall -Wextra $< $(TARGET_DIR)/libwaterboxhost.dylib \
		-Wl,-rpath,@loader_path -o $@

run: all
	cd $(TARGET_DIR) && ./waterbox-smoke smoke.wbx

clean:
	rm -f $(TARGET_DIR)/smoke.wbx $(TARGET_DIR)/waterbox-smoke
