
use super::MemoryBlock;
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use crate::*;
use super::*;
use lazy_static::lazy_static;

#[cfg(target_os = "macos")]
static DARWIN_CONFLICT_COUNT: AtomicUsize = AtomicUsize::new(0);

#[cfg(target_os = "macos")]
static DARWIN_FAULT_PC: AtomicUsize = AtomicUsize::new(0);

#[cfg(target_os = "macos")]
static DARWIN_FAULT_ARGS: [AtomicUsize; 3] = [
	AtomicUsize::new(0),
	AtomicUsize::new(0),
	AtomicUsize::new(0),
];

#[cfg(target_os = "macos")]
static DARWIN_LAST_UNHANDLED: [AtomicUsize; 3] = [
	AtomicUsize::new(0), // address
	AtomicUsize::new(0), // pc
	AtomicUsize::new(0), // sp
];

/// Async-signal-safe diagnostic writer.  `eprintln!`/`format!` call the global allocator and lock
/// stdio, neither of which is safe to reach from a signal handler: if the interrupted thread was
/// itself inside `malloc`/`free` (holding its internal zone lock) when the fault landed, a
/// reentrant allocation from the handler corrupts the allocator's bookkeeping instead of
/// deadlocking, and the corruption only surfaces later, on some unrelated thread's next
/// allocation (this is what a Darwin SIGBUS *inside* `libsystem_malloc.dylib` with a completely
/// unrelated fault address indicates).  These helpers only call `write(2)`, which is
/// async-signal-safe, and format into a fixed-size on-stack buffer -- no heap involved.
#[cfg(target_os = "macos")]
pub(crate) unsafe fn sig_write(bytes: &[u8]) {
	libc::write(2, bytes.as_ptr() as *const libc::c_void, bytes.len());
}

/// Formats `value` as a zero-padded lowercase hex literal (e.g. `0x00001234`) into `out` and
/// returns the written slice.  No allocation.
#[cfg(target_os = "macos")]
unsafe fn hex_into<'a>(out: &'a mut [u8; 18], value: usize) -> &'a [u8] {
	const DIGITS: &[u8; 16] = b"0123456789abcdef";
	out[0] = b'0';
	out[1] = b'x';
	for i in 0..16 {
		let shift = (15 - i) * 4;
		out[2 + i] = DIGITS[(value >> shift) & 0xf];
	}
	out
}

#[cfg(target_os = "macos")]
pub(crate) unsafe fn sig_write_hex(label: &[u8], value: usize) {
	let mut buf = [0u8; 18];
	sig_write(label);
	sig_write(hex_into(&mut buf, value));
}

lazy_static! {
	static ref GLOBAL_DATA: Mutex<GlobalData> = Mutex::new(GlobalData {
		initialized: false,
		active_blocks: Vec::new(),
	});
}

struct GlobalData {
	initialized: bool,
	active_blocks: Vec<MemoryBlockRef>, 
}

pub unsafe fn register(block: *mut MemoryBlock) {
	let mut data = GLOBAL_DATA.lock().unwrap();
	if !data.initialized {
		trip_pal::initialize();
		data.initialized = true;
	}
	#[cfg(target_os = "macos")]
	trip_pal::prepare_thread();
	data.active_blocks.push(MemoryBlockRef(block));
}

pub unsafe fn unregister(block: *mut MemoryBlock) {
	let mut data = GLOBAL_DATA.lock().unwrap();
	let pos = data.active_blocks.iter().position(|x| x.0 == block);
	match pos {
		Some(index) => {
			data.active_blocks.remove(index);
		},
		None => {
			panic!("Tried to unregister MemoryBlock which was not registered")
		}
	}
}

enum TripResult {
	Handled,
	NotHandled,
}

unsafe fn trip(addr: usize) -> TripResult {
	let data = GLOBAL_DATA.lock().unwrap();
	let memory_block = match data.active_blocks
		.iter()
		.find(|x| (*x.0).addr.contains(addr)) {
			Some(x) => &mut *x.0,
			None => {
				return TripResult::NotHandled
			},
		};

	#[cfg(target_os = "macos")]
	let (protected_addr, first_page, page_count) = {
		let host_size = host_page_size();
		let host_start = addr & !(host_size - 1);
		(AddressRange { start: host_start, size: host_size },
			(host_start - memory_block.addr.start) >> GUEST_PAGE_SHIFT,
			host_size / GUEST_PAGE_SIZE)
	};
	#[cfg(not(target_os = "macos"))]
	let (protected_addr, first_page, page_count) = {
		let page_start = addr & !GUEST_PAGE_MASK;
		(AddressRange { start: page_start, size: GUEST_PAGE_SIZE },
			(addr - memory_block.addr.start) >> GUEST_PAGE_SHIFT,
			1)
	};

	let pages = &mut memory_block.pages[first_page..first_page + page_count];
	// On macOS a host page can legally group guest sub-pages with mixed status (e.g. three
	// Allocated(RW) pages next to a Free one): only the specific sub-page that faulted needs to
	// be writable, not every sub-page sharing the host page.  Elsewhere (Linux/Windows) each
	// host page is exactly one guest page, so this degenerates to the old same-status check.
	let fault_index = (addr - protected_addr.start) >> GUEST_PAGE_SHIFT;
	let status = pages[fault_index].status;
	if !status.writable() {
		#[cfg(target_os = "macos")]
		if DARWIN_CONFLICT_COUNT.fetch_add(1, Ordering::Relaxed) < 8 {
			// Async-signal-safe: no `eprintln!`/`format!`/`Vec` here, see `sig_write` above.
			sig_write(b"[conflict] addr=");
			sig_write_hex(b"", addr);
			sig_write_hex(b" pc=", DARWIN_FAULT_PC.load(Ordering::Relaxed));
			sig_write_hex(b" x0=", DARWIN_FAULT_ARGS[0].load(Ordering::Relaxed));
			sig_write_hex(b" x1=", DARWIN_FAULT_ARGS[1].load(Ordering::Relaxed));
			sig_write_hex(b" x2=", DARWIN_FAULT_ARGS[2].load(Ordering::Relaxed));
			sig_write_hex(b" prev_addr=", DARWIN_LAST_UNHANDLED[0].load(Ordering::Relaxed));
			sig_write_hex(b" prev_pc=", DARWIN_LAST_UNHANDLED[1].load(Ordering::Relaxed));
			sig_write_hex(b" prev_sp=", DARWIN_LAST_UNHANDLED[2].load(Ordering::Relaxed));
			sig_write_hex(b" host=", protected_addr.start);
			sig_write_hex(b" first_page=", first_page);
			sig_write(b" statuses=");
			for page in pages.iter() {
				sig_write_hex(b"", MemoryBlock::status_bits(page.status) as usize);
			}
			sig_write(b" dirty=");
			for page in pages.iter() {
				sig_write(if page.dirty { b"1" } else { b"0" });
			}
			sig_write(b"\n");
		}
		std::intrinsics::breakpoint();
		return TripResult::NotHandled
	}
	for (index, page) in pages.iter_mut().enumerate() {
		// A Free neighbor sharing this host page has no meaningful content to snapshot and
		// is not tracked as dirty; only touch the sub-pages actually allocated to the guest.
		if page.status == PageAllocation::Free {
			continue
		}
		page.maybe_snapshot(protected_addr.start + index * GUEST_PAGE_SIZE);
		page.dirty = true;
	}
	#[cfg(target_os = "macos")]
	let new_prot = MemoryBlock::host_page_protection(pages);
	#[cfg(not(target_os = "macos"))]
	let new_prot = pages[fault_index].native_prot();
	match pal::protect(protected_addr, new_prot) {
		Ok(()) => {
			TripResult::Handled
		},
		_ => {
			std::intrinsics::breakpoint();
			std::process::abort();
		}
	}
}

#[cfg(windows)]
mod trip_pal {
	use super::*;
	use winapi::um::errhandlingapi::*;
	use winapi::um::winnt::*;
	use winapi::vc::excpt::*;

	pub fn initialize() {
		unsafe extern "system" fn handler(p_info: *mut EXCEPTION_POINTERS) -> i32 {
			let p_record = &*(*p_info).ExceptionRecord;
			let flags = p_record.ExceptionInformation[0];
			match p_record.ExceptionCode {
				STATUS_ACCESS_VIOLATION if (flags & 1) != 0 => (), // write exception
				STATUS_GUARD_PAGE_VIOLATION => {
					// guard exception

					// If this is ours, it's from a cothread stack.  If we're on a cothread
					// stack right now, we need to return without taking our lock because
					// the stack used in handler() and everything it calls might move onto
					// another page and trip again which would deadlock us.  (Alternatively,
					// this might be the second trip in a row while already servicing another one.)
					// This does not cause determinism issues because of get_stack_dirty() and the
					// windows specific code in set_protections().

					// The only problem here is that we might be swallowing a completely unrelated guard
					// exception by returning before checking whether it was in a waterbox block;
					// in which case we'll find out what we broke eventually.
					return EXCEPTION_CONTINUE_EXECUTION 
				},
				_ => return EXCEPTION_CONTINUE_SEARCH
			}
			let fault_address = p_record.ExceptionInformation[1] as usize;
			match trip(fault_address) {
				TripResult::Handled => EXCEPTION_CONTINUE_EXECUTION,
				TripResult::NotHandled => EXCEPTION_CONTINUE_SEARCH,
			}
		}
		unsafe {
			let res = AddVectoredExceptionHandler(1 /* CALL_FIRST */, Some(handler));
			assert!(!res.is_null(), "AddVectoredExceptionHandler failed");
		}
	}
}

#[cfg(target_os = "linux")]
mod trip_pal {
	use libc::*;
	use super::*;

	type SaHandler = unsafe extern "C" fn(i32) -> ();
	type SaSigaction = unsafe extern "C" fn(i32, *const siginfo_t, *const ucontext_t) -> ();
	static mut SA_OLD: Option<Box<sigaction>> = None;

	pub fn initialize() {
		use std::mem::{transmute, zeroed};

		#[cfg(target_arch = "x86_64")]
		unsafe fn can_be_dirty_write(ucontext: *const ucontext_t) -> bool {
			(*ucontext).uc_mcontext.gregs[REG_ERR as usize] & 2 != 0
		}

		#[cfg(target_arch = "aarch64")]
		unsafe fn can_be_dirty_write(_ucontext: *const ucontext_t) -> bool {
			// Linux's libc ucontext does not expose a portable read/write fault bit on
			// AArch64. Waterbox only removes write access from logically writable pages,
			// and trip() rejects every other page status, so trying the address is the
			// exact classification needed here.
			true
		}

		unsafe extern "C" fn handler(sig: i32, info: *const siginfo_t, ucontext: *const ucontext_t) {
			let fault_address = (*info).si_addr() as usize;
			let rethrow = !can_be_dirty_write(ucontext) || match trip(fault_address) {
				TripResult::NotHandled => true,
				_ => false
			};
			if rethrow {
				let sa_old = SA_OLD.as_ref().unwrap();
				if sa_old.sa_flags & SA_SIGINFO != 0 {
					transmute::<usize, SaSigaction>(sa_old.sa_sigaction)(sig, info, ucontext);
				} else {
					transmute::<usize, SaHandler>(sa_old.sa_sigaction)(sig);
				}
			}
		}
		unsafe {
			// TODO: sigaltstack is per thread, so this won't work
			// At the same time, one seems to be set up automatically on each thread, so this isn't needed.
			// let ss = stack_t {
			// 	ss_flags: 0,
			// 	ss_sp: Box::into_raw(Box::new(zeroed::<[u8; SIGSTKSZ]>())) as *mut c_void,
			// 	ss_size: SIGSTKSZ
			// };
			// let mut ss_old = stack_t {
			// 	ss_flags: 0,
			// 	ss_sp: 0 as *mut c_void,
			// 	ss_size: 0
			// };
			// assert!(sigaltstack(&ss, &mut ss_old) == 0, "sigaltstack failed");
			SA_OLD = Some(Box::new(zeroed()));
			let mut sa = sigaction {
				sa_mask: zeroed(),
				sa_sigaction: transmute::<SaSigaction, usize>(handler),
				sa_flags: SA_ONSTACK | SA_SIGINFO,
				sa_restorer: None,
			};
			sigfillset(&mut sa.sa_mask);
			assert!(sigaction(SIGSEGV, &sa, &mut **SA_OLD.as_mut().unwrap() as *mut sigaction) == 0, "sigaction failed");
		}
	}
}

#[cfg(target_os = "macos")]
mod trip_pal {
	use libc::*;
	use super::*;
	use std::mem::{transmute, zeroed};
	use std::ptr::{null, null_mut};
	use std::sync::OnceLock;

	type SaHandler = unsafe extern "C" fn(i32) -> ();
	type SaSigaction = unsafe extern "C" fn(i32, *const siginfo_t, *mut c_void) -> ();
	static SA_OLD_SEGV: OnceLock<sigaction> = OnceLock::new();
	static SA_OLD_BUS: OnceLock<sigaction> = OnceLock::new();
	thread_local! {
		static ALT_STACK: std::cell::RefCell<Option<Box<[u8]>>> = std::cell::RefCell::new(None);
	}

	unsafe extern "C" fn handler(sig: i32, info: *const siginfo_t, ucontext: *mut c_void) {
		let fault_address = (*info).si_addr() as usize;
		let machine_context = (*(ucontext as *const ucontext_t)).uc_mcontext;
		DARWIN_FAULT_PC.store((*machine_context).__ss.__pc as usize, Ordering::Relaxed);
		for (destination, source) in DARWIN_FAULT_ARGS.iter().zip((&(*machine_context).__ss.__x)[..3].iter()) {
			destination.store(*source as usize, Ordering::Relaxed);
		}
		if let TripResult::Handled = trip(fault_address) {
			return
		}
		// Async-signal-safe: `eprintln!`/`format!` call the global allocator and lock stdio.
		// This handler runs for *every* SIGSEGV/SIGBUS in the process, including ones CoreCLR
		// generates for its own unrelated purposes (JIT null-check probing, GC card-table
		// write-barrier faults, ...), so this path is reached far more often than only genuine
		// tripguard misses.  Reentering `malloc` here, if the interrupted thread happened to be
		// inside `malloc`/`free` already, corrupts the allocator's heap instead of merely
        // failing -- the corruption then only surfaces later, as an unrelated SIGBUS deep inside
		// libsystem_malloc.dylib on some other thread's next allocation.
		sig_write(b"[unhandled] sig=");
		sig_write_hex(b"", sig as usize);
		sig_write_hex(b" addr=", fault_address);
		sig_write_hex(b" pc=", (*machine_context).__ss.__pc as usize);
		sig_write_hex(b" sp=", (*machine_context).__ss.__sp as usize);
		sig_write_hex(b" x28=", (*machine_context).__ss.__x[28] as usize);
		sig_write(b"\n");
		DARWIN_LAST_UNHANDLED[0].store(fault_address, Ordering::Relaxed);
		DARWIN_LAST_UNHANDLED[1].store((*machine_context).__ss.__pc as usize, Ordering::Relaxed);
		DARWIN_LAST_UNHANDLED[2].store((*machine_context).__ss.__sp as usize, Ordering::Relaxed);

		let sa_old = if sig == SIGSEGV {
			SA_OLD_SEGV.get().unwrap()
		} else {
			SA_OLD_BUS.get().unwrap()
		};
		if sa_old.sa_sigaction == SIG_DFL || sa_old.sa_sigaction == SIG_IGN {
			sigaction(sig, sa_old, null_mut());
			raise(sig);
		} else if sa_old.sa_flags & SA_SIGINFO != 0 {
			transmute::<usize, SaSigaction>(sa_old.sa_sigaction)(sig, info, ucontext);
		} else {
			transmute::<usize, SaHandler>(sa_old.sa_sigaction)(sig);
		}
	}

	pub fn initialize() {
		unsafe {
			let mut sa: sigaction = zeroed();
			sa.sa_sigaction = transmute::<SaSigaction, usize>(handler);
			sa.sa_flags = SA_ONSTACK | SA_SIGINFO;
			sigfillset(&mut sa.sa_mask);

			let mut old_segv: sigaction = zeroed();
			assert!(sigaction(SIGSEGV, null(), &mut old_segv) == 0, "querying SIGSEGV sigaction failed");
			assert!(SA_OLD_SEGV.set(old_segv).is_ok());
			assert!(sigaction(SIGSEGV, &sa, null_mut()) == 0, "SIGSEGV sigaction failed");

			let mut old_bus: sigaction = zeroed();
			assert!(sigaction(SIGBUS, null(), &mut old_bus) == 0, "querying SIGBUS sigaction failed");
			assert!(SA_OLD_BUS.set(old_bus).is_ok());
			assert!(sigaction(SIGBUS, &sa, null_mut()) == 0, "SIGBUS sigaction failed");
		}
	}

	pub fn prepare_thread() {
		ALT_STACK.with(|alt_stack| {
			let mut alt_stack = alt_stack.borrow_mut();
			if alt_stack.is_some() {
				return
			}
			unsafe {
				let mut current: stack_t = zeroed();
				assert_eq!(sigaltstack(null(), &mut current), 0, "querying alternate signal stack failed");
				if current.ss_flags & SS_DISABLE == 0 {
					return
				}
				let mut storage = vec![0u8; 64 * 1024].into_boxed_slice();
				let stack = stack_t {
					ss_sp: storage.as_mut_ptr() as *mut c_void,
					ss_size: storage.len(),
					ss_flags: 0,
				};
				assert_eq!(sigaltstack(&stack, null_mut()), 0, "installing alternate signal stack failed");
				*alt_stack = Some(storage);
			}
		});
	}
}
