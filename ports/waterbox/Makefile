# Receta WBX AArch64 de puae6.wbx (T19a). No se invoca a mano: tools/build_waterbox.py --puae6 pasa REPO, ROOT_DIR, OUT_DIR,
# SOURCES_MK y la cadena de herramientas fijada, y ejecuta make con la raiz del repositorio como directorio de trabajo para que los
# objetos reflejen la ruta de cada fuente. Usa el common.mak y el linkscript_aarch64.T capturados de MacHawk sin cambiarlos:
# -ffixed-x18 -ffixed-x28, paginas de 16 KiB y el resto de los flags del contrato salen de ahi para TODAS las unidades.
TARGET := puae6.wbx
WBX_TARGET_ARCH := aarch64
WBX_HOST_PAGE_SIZE := 16384
include $(SOURCES_MK)

# Mismos flags que el build nativo (CMakeLists.txt): C11/C++17 y sin contraccion de coma flotante, que cambiaria el ultimo bit de
# Paula en una fraccion de las muestras. Los include son los de los objetivos nativos.
FP := -fno-fast-math -ffp-contract=off
INC := -I$(REPO)/ports/portable -I$(REPO)/ports/portable/media -I$(REPO)/build/generated -I$(REPO)/build/upstream/include \
	-I$(REPO)/build/upstream -I$(REPO)/bridge/include
CCFLAGS := -std=c11 $(FP) $(INC)
CXXFLAGS := -std=c++17 $(FP) $(INC)

override EMULIBC_OBJS = $(OBJ_DIR)/emulibc.c.o
include $(REPO)/references/machawk/waterbox/common.mak

# Se compila emulibc en nuestro arbol de salida, nunca dentro de references/MacHawk.
$(OBJ_DIR)/emulibc.c.o: $(WATERBOX_DIR)/emulibc/emulibc.c
	@mkdir -p $(@D)
	$(CC) -c -o $@ $< $(CCFLAGS) $(CCFLAGS_RELEASE)
