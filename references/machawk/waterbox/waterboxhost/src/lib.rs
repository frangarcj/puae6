#![crate_type = "cdylib"]

#![feature(try_trait_v2)]
#![feature(try_trait_v2_residual)]
#![feature(never_type)]
#![feature(core_intrinsics)]

#![allow(dead_code)]

use std::io::{Read, Write};
use anyhow::anyhow;
use syscall_defs::{SyscallReturn};

// These are part of Waterbox's guest memory model and its state format.
const GUEST_PAGE_SIZE: usize = 0x1000;
const GUEST_PAGE_MASK: usize = GUEST_PAGE_SIZE - 1;
const GUEST_PAGE_SHIFT: i32 = 12;

fn host_page_size() -> usize {
	page_size::get()
}

mod memory_block;
mod syscall_defs;
mod bin;
mod elf;
mod fs;
mod host;
mod cinterface;
mod gdb;
mod context;
mod threading;
#[cfg(target_arch = "x86_64")]
mod calling_convention_adapters;

pub trait IStateable {
	fn save_state(&mut self, stream: &mut dyn Write) -> anyhow::Result<()>;
	fn load_state(&mut self, stream: &mut dyn Read) -> anyhow::Result<()>;
}

#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AddressRange {
	pub start: usize,
	pub size: usize,
}
impl AddressRange {
	pub fn end(&self) -> usize {
		self.start + self.size
	}
	pub fn contains(&self, addr: usize) -> bool {
		addr >= self.start && addr < self.end()
	}
	/// Unsafe: Pointers are unchecked and lifetime is not connected to the AddressRange
	pub unsafe fn slice(&self) -> &'static [u8] {
		std::slice::from_raw_parts(self.start as *const u8, self.size)
	}
	/// Unsafe: Pointers are unchecked and lifetime is not connected to the AddressRange
	pub unsafe fn slice_mut(&self) -> &'static mut [u8] {
		std::slice::from_raw_parts_mut(self.start as *mut u8, self.size)
	}
	/// Unsafe: Pointers are unchecked and mut is not required (TODO: but why?)
	pub unsafe fn zero(&self) {
		std::ptr::write_bytes(self.start as *mut u8, 0, self.size);
	}
	/// Expands an address range to page alignment
	pub fn align_expand(&self) -> AddressRange {
		return AddressRange {
			start: self.start & !GUEST_PAGE_MASK,
			size: ((self.end().wrapping_sub(1) | GUEST_PAGE_MASK).wrapping_add(1))
				- (self.start & !GUEST_PAGE_MASK),
		}
	}
}
impl IStateable for AddressRange {
	fn save_state(&mut self, stream: &mut dyn Write) -> anyhow::Result<()> {
		bin::write(stream, &self.start)?;
		bin::write(stream, &self.size)?;
		Ok(())
	}
	fn load_state(&mut self, stream: &mut dyn Read) -> anyhow::Result<()> {
		bin::read(stream, &mut self.start)?;
		bin::read(stream, &mut self.size)?;
		Ok(())
	}
}

fn align_down(p: usize) -> usize {
	p & !GUEST_PAGE_MASK
}
fn align_up(p: usize) -> usize {
	((p.wrapping_sub(1)) | GUEST_PAGE_MASK).wrapping_add(1)
}

/// Information about memory layout injected into the guest application
#[repr(C)]
#[derive(Copy, Clone)]
pub struct WbxSysLayout {
	// Keep this all in sync with the C code!

	pub elf: AddressRange,
	pub main_thread: AddressRange,
	pub alt_thread: AddressRange,
	pub sbrk: AddressRange,
	pub sealed: AddressRange,
	pub invis: AddressRange,
	pub plain: AddressRange,
	pub mmap: AddressRange,
}
impl WbxSysLayout {
	pub fn all(&self) -> AddressRange {
		AddressRange {
			start: self.elf.start,
			size: self.mmap.end() - self.elf.start
		}
	}
}

/// Always remove memoryblocks from active ram when possible, to help debug dangling pointers.
/// Severe performance consequences.
static mut ALWAYS_EVICT_BLOCKS: bool = true;

#[cfg(test)]
mod tests {
	#[test]
	fn test_pagesize() {
		#[cfg(not(target_os = "macos"))]
		assert_eq!(crate::GUEST_PAGE_SIZE, crate::host_page_size());
		#[cfg(target_os = "macos")]
		{
			assert_eq!(crate::GUEST_PAGE_SIZE, 0x1000);
			assert_eq!(crate::host_page_size(), 0x4000);
		}
	}
}
