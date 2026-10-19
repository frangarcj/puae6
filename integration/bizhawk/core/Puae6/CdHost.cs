using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using BizHawk.BizInvoke;
using BizHawk.Common;
using BizHawk.Emulation.Cores.Waterbox;
namespace Puae6;
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6CdInfo
{
 public uint Version, Size;
 public fixed byte Id[32];
 public ulong Bytes;
 public uint Count, Leadout;
 public fixed uint Tracks[99 * 6];
}
public abstract partial class LibPuae6
{
 [UnmanagedFunctionPointer(CC)] public delegate int CdRead(ulong offset, IntPtr destination, uint count);
 [BizImport(CC)] public abstract ulong p6w_cd_layout(IntPtr output, ulong capacity);
 [BizImport(CC)] public abstract int p6w_cd_bind(IntPtr info, CdRead callback, uint rebind);
 [BizImport(CC)] public abstract int p6w_cd_read_at(ulong offset, IntPtr output, uint count);
 [BizImport(CC)] public abstract int p6w_cd_read_sector(uint lba, uint view, IntPtr output, uint capacity);
}
// One borrowed provider per sandbox; its identity and TOC are fixed for this host.
public unsafe sealed class P6CdHost
{
 readonly P6CdInfo info;
 readonly Func<ulong, byte[], int, bool> read;
 readonly LibPuae6.CdRead callback;
 WaterboxHost host;
 bool faulted;
 public P6CdHost(P6CdInfo descriptor, Func<ulong, byte[], int, bool> provider)
  { info = descriptor; read = provider ?? throw new ArgumentNullException(nameof(provider)); callback = Read; }
 internal Delegate[] Callbacks(WaterboxHost owner)
 {
  if (host != null) throw new InvalidOperationException("A CD binding belongs to one sandbox");
  host = owner; return new Delegate[] { callback }; // Stable slot0, registered before BizInvoker.
 }
 int Read(ulong at, IntPtr destination, uint count)
 {
  try {
   if (count > 2448 || faulted) return -P6.Io;
   var data = new byte[count];
   if (!read(at, data, (int)count)) return -P6.Io;
   host.CopyFromExternal(data, destination); return (int)count;
  } catch (Exception) { return -P6.Io; } // Never unwind managed exceptions through the guest.
 }
 internal void Attach(Puae6Guest guest)
 {
  using var active = guest.Host.EnterExit();
  ulong* layout = stackalloc ulong[3];
  if (guest.Lib.p6w_cd_layout((IntPtr)layout, 3) != 3 || layout[0] != (ulong)sizeof(P6CdInfo)
   || layout[1] != (ulong)Marshal.OffsetOf<P6CdInfo>(nameof(P6CdInfo.Bytes)).ToInt64()
   || layout[2] != (ulong)Marshal.OffsetOf<P6CdInfo>(nameof(P6CdInfo.Tracks)).ToInt64()) throw new GuestLayoutException("CD descriptor layout differs");
 }
 public int Bind(Puae6Guest guest, bool rebind, P6CdInfo? candidate = null)
 {
  if (!ReferenceEquals(host, guest.Host)) return P6.BadArgument;
  if (!guest.IsSealed) return P6.BadState;
  using var active = guest.Host.EnterExit();
  var descriptor = candidate ?? info; return guest.Lib.p6w_cd_bind((IntPtr)(&descriptor), callback, rebind ? 1u : 0u);
 }
 public int ReadAt(Puae6Guest guest, ulong offset, byte[] output)
 {
  if (!ReferenceEquals(host, guest.Host)) return P6.BadArgument;
  if (faulted) return P6.BadState;
  using var active = guest.Host.EnterExit();
  fixed (byte* p = output) return guest.Lib.p6w_cd_read_at(offset, (IntPtr)p, (uint)output.Length);
 }
 public void Restore(Puae6Guest guest, BinaryReader input, StateIdentity expected)
 {
  if (!ReferenceEquals(host, guest.Host)) throw new ArgumentException("Wrong sandbox");
  if (!guest.IsSealed) throw new InvalidOperationException("Seal the CD baseline before restoring");
  var payload = StateEnvelope.Read(input, expected); // Reject identity/checksum before touching sandbox.
  using var stream = new MemoryStream(payload, false); using var snapshot = new BinaryReader(stream);
  using var active = guest.Host.EnterExit();
  faulted = true;
  guest.Host.LoadStateBinary(snapshot);
  if (Bind(guest, true) != P6.Ok) throw new InvalidDataException("Restored CD binding refused");
  faulted = false;
 }
}
