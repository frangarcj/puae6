use crate::syscall_defs::*;
use crate::*;
use std::io::{Write, Read};
use super::*;

/// A file whose content is in memory and managed by the waterbox host
pub struct RegularFile {
	data: Vec<u8>,
	hash: Option<Vec<u8>>,
	position: usize,
	dirty: bool,
}
impl RegularFile {
	pub fn new(data: Vec<u8>, writable: bool) -> RegularFile {
		let hash = if writable {
			None
		} else {
			Some(bin::hash(&data[..]))
		};
		RegularFile {
			data,
			hash,
			position: 0,
			dirty: false,
		}
	}
}
impl IStateable for RegularFile {
	fn save_state(&mut self, stream: &mut dyn Write) -> anyhow::Result<()> {
		match &self.hash {
			Some(hash) => {
				bin::write_magic(stream, "RegularFile")?;
				bin::write_hash(stream, &hash[..])?;
				bin::write(stream, &self.position)?;
				Ok(())
			},
			None => {
				bin::write_magic(stream, "RegularFile(RW)")?;
				bin::writeval(stream, self.data.len())?;
				stream.write_all(&self.data[..])?;
				bin::write(stream, &self.position)?;
				bin::write(stream, &self.dirty)?;
				Ok(())
			},
		}
	}
	fn load_state(&mut self, stream: &mut dyn Read) -> anyhow::Result<()> {
		match &self.hash {
			Some(hash) => {
				bin::verify_magic(stream, "RegularFile")?;
				bin::verify_hash(stream, &hash[..])?;
				bin::read(stream, &mut self.position)?;
				Ok(())
			}
			None => {
				bin::verify_magic(stream, "RegularFile(RW)")?;
				let len = bin::readval(stream)?;
				if len != self.data.len() {
					self.data.resize(len, 0);
				}
				stream.read_exact(&mut self.data[..])?;
				bin::read(stream, &mut self.position)?;
				bin::read(stream, &mut self.dirty)?;
				Ok(())
			},
		}
	}
}
impl FileObject for RegularFile {
	fn can_read(&self) -> bool {
		true
	}
	fn read(&mut self, buf: &mut [u8]) -> Result<i64, SyscallError> {
		let n = std::cmp::min(buf.len(), self.data.len() - self.position);
		let dst = &mut buf[0..n];
		let src = &self.data[self.position..self.position + n];
		dst.copy_from_slice(src);
		self.position += n;
		Ok(n as i64)
	}
	fn can_write(&self) -> bool {
		match self.hash {
			None => true,
			Some(_) => false	
		}	
	}
	fn write(&mut self, buf: &[u8]) -> Result<i64, SyscallError> {
		if !self.can_write() {
			return Err(EBADF)
		}
		let n = buf.len();
		let newpos = self.position + n;
		let changed = newpos > self.data.len()
			|| &self.data[self.position..newpos] != buf;
		if newpos > self.data.len() {
			self.data.resize(newpos, 0);
		}
		let dst = &mut self.data[self.position..newpos];

		dst.copy_from_slice(buf);
		self.position = newpos;
		self.dirty |= changed;
		Ok(n as i64)
	}
	fn seek(&mut self, offset: i64, whence: i32) -> Result<i64, SyscallError> {
		let newpos = match whence {
			SEEK_SET => {
				offset
			},
			SEEK_CUR => {
				self.position as i64 + offset
			},
			SEEK_END => {
				self.data.len() as i64 + offset
			}
			_ => return Err(EINVAL)
		};
		if newpos < 0 || newpos > self.data.len() as i64 {
			return Err(EINVAL)
		}
		self.position = newpos as usize;
		Ok(newpos)
	}
	fn truncate(&mut self, size: i64) -> SyscallResult {
		if !self.can_write() {
			return Err(EBADF)
		}
		if size < 0 {
			return Err(EINVAL)
		}
		self.dirty |= self.data.len() != size as usize;
		self.data.resize(size as usize, 0);
		self.position = std::cmp::min(self.position, size as usize);
		Ok(())
	}
	fn reset(&mut self) {
		self.position = 0;
	}
	fn stat(&self, statbuff: &mut KStat) -> SyscallResult {
		fill_stat(statbuff, true, self.can_write(), true, self.data.len() as i64)
	}
	fn can_unmount(&self) -> bool {
		true
	}
	fn unmount(self: Box<Self>) -> Vec<u8> {
		self.data
	}
	fn mounted_data(&self) -> Option<&[u8]> {
		Some(&self.data)
	}
	fn replace_mounted_data(&mut self, data: &[u8]) -> bool {
		if !self.can_write() {
			return false
		}
		self.data.clear();
		self.data.extend_from_slice(data);
		self.position = std::cmp::min(self.position, self.data.len());
		self.dirty = false;
		true
	}
	fn mounted_data_modified(&self) -> Option<bool> {
		if self.can_write() { Some(self.dirty) } else { None }
	}
	fn clear_mounted_data_modified(&mut self) -> bool {
		if !self.can_write() {
			return false
		}
		self.dirty = false;
		true
	}
}
