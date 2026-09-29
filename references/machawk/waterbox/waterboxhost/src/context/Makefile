LLVM_MC ?= llvm-mc
LD_LLD ?= ld.lld
LLVM_OBJCOPY ?= llvm-objcopy

interop.bin: interop.s
	nasm -f bin -Wall -o $@ $<

interop_aarch64.bin: interop_aarch64.s interop_aarch64_link.ld
	$(LLVM_MC) -triple=aarch64-linux-gnu -filetype=obj -o interop_aarch64.o $<
	$(LD_LLD) -T interop_aarch64_link.ld -o interop_aarch64.elf interop_aarch64.o
	$(LLVM_OBJCOPY) -O binary interop_aarch64.elf $@
	rm -f interop_aarch64.o interop_aarch64.elf
