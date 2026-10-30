# Receta WBX de puae6.wbx (T19a/T83a), por defecto AArch64. Los builders pasan REPO, ROOT_DIR, OUT_DIR,
# SOURCES_MK y la cadena de herramientas fijada, y ejecuta make con la raiz del repositorio como directorio de trabajo para que los
# objetos reflejen la ruta de cada fuente. Usa el common.mak y el linkscript_aarch64.T capturados de MacHawk sin cambiarlos:
# -ffixed-x18 -ffixed-x28, paginas de 16 KiB y el resto de los flags del contrato salen de ahi para TODAS las unidades.
TARGET := puae6.wbx
ifndef WBX_TARGET_ARCH
WBX_TARGET_ARCH := aarch64
endif
ifndef WBX_HOST_PAGE_SIZE
ifeq ($(WBX_TARGET_ARCH),aarch64)
WBX_HOST_PAGE_SIZE := 16384
else
WBX_HOST_PAGE_SIZE := 4096
endif
endif
ifeq ($(WBX_TARGET_ARCH)/$(WBX_HOST_PAGE_SIZE),aarch64/16384)
else ifeq ($(WBX_TARGET_ARCH)/$(WBX_HOST_PAGE_SIZE),x86_64/4096)
else
$(error Unsupported PUAE6 Waterbox target/page pair: $(WBX_TARGET_ARCH)/$(WBX_HOST_PAGE_SIZE))
endif
include $(SOURCES_MK)

# Mismos flags que el build nativo (CMakeLists.txt): C11/C++17 y sin contraccion de coma flotante, que cambiaria el ultimo bit de
# Paula en una fraccion de las muestras. Los include son los de los objetivos nativos.
FP := -fno-fast-math -ffp-contract=off
INC := -I$(REPO)/ports/portable -I$(REPO)/ports/portable/media -I$(REPO)/build/generated -I$(REPO)/build/upstream/include \
	-I$(REPO)/build/upstream -I$(REPO)/bridge/include -I$(REPO)/adapters/waterbox \
	-I$(REPO)/build/libmpeg2/include -I$(REPO)/build/libmpeg2/src \
	-I$(REPO)/build/libmpeg2/src/convert
# The native CMake target defines the bounded software RTG backend too. Keep the
# same definition on every Waterbox unit so fixup_prefs preserves board zero.
PORTABLE_FEATURES := -DP6_PORTABLE_RTG=1
CCFLAGS := -std=c11 $(FP) $(PORTABLE_FEATURES) $(INC)
CXXFLAGS := -std=c++17 $(FP) $(PORTABLE_FEATURES) $(INC)

# T58: the IPF reader (ipf_track.cpp, with the decoder of MAME included in it) uses std::vector, whose throw sites import C++ runtime names that imports.json does not allow in the guest:
# it is compiled without exceptions (a failed allocation is the sandbox's, as for any allocation in the guest; see ipf_track.cpp). The path is that of the source in SRCS.
PER_FILE_FLAGS_ports/portable/media/ipf_track.cpp := -fno-exceptions

override EMULIBC_OBJS = $(OBJ_DIR)/emulibc.c.o
include $(REPO)/references/machawk/waterbox/common.mak

# Se compila emulibc en nuestro arbol de salida, nunca dentro de references/MacHawk.
$(OBJ_DIR)/emulibc.c.o: $(WATERBOX_DIR)/emulibc/emulibc.c
	@mkdir -p $(@D)
	$(CC) -c -o $@ $< $(CCFLAGS) $(CCFLAGS_RELEASE)
