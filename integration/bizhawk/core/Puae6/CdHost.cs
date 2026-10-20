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
// Contract only; v1 imports, callback slots and binding remain unchanged.
// Modes0/1/2: Audio/Mode1Raw/Mode2Raw. Stored/SUB refer to canonical wire data,
// including supplied Q/SBI, rather than physical image packing. Inactive fields0.
[StructLayout(LayoutKind.Sequential)]
public struct P6CdTrackV2
{
 public uint Index0, Index1, End, Mode, Control, Reserved;
 public uint StoredPregap, SyntheticPregap, Postgap, Flags, PregapMode, PregapControl;
}
[StructLayout(LayoutKind.Sequential)]
public unsafe struct P6CdInfoV2
{
 public uint Version, Size;
 public fixed byte Id[32];
 public ulong Bytes;
 public uint Count, Leadout;
 public fixed uint Tracks[99 * 12]; // 48-byte P6CdTrackV2 records; version2, size4808.
}
public abstract partial class LibPuae6
{
 [UnmanagedFunctionPointer(CC)] public delegate int CdRead(ulong offset, IntPtr destination, uint count);
 [UnmanagedFunctionPointer(CC)] public delegate int CdCatalogRead(uint disc, ulong offset, IntPtr destination, uint count);
 [BizImport(CC)] public abstract int p6w_cd_catalog_bind(IntPtr info, uint count, CdCatalogRead callback, uint rebind);
 [BizImport(CC)] public abstract int p6w_cd_change(uint index, uint inserted);
 [BizImport(CC)] public abstract int p6w_cd_status(IntPtr output, uint capacity);
 [BizImport(CC)] public abstract ulong p6w_cd_layout(IntPtr output, ulong capacity);
 [BizImport(CC)] public abstract int p6w_cd_bind(IntPtr info, CdRead callback, uint rebind);
 [BizImport(CC)] public abstract int p6w_cd_activate();
 [BizImport(CC)] public abstract int p6w_cd_read_at(ulong offset, IntPtr output, uint count);
 [BizImport(CC)] public abstract int p6w_cd_read_sector(uint lba, uint view, IntPtr output, uint capacity);
}
// One borrowed provider per sandbox; its identity and TOC are fixed for this host.
public unsafe sealed class P6CdHost
{
 readonly P6CdInfo info;
 readonly Func<ulong, byte[], int, bool> read;
 readonly LibPuae6.CdRead callback;
 readonly LibPuae6.CdCatalogRead catalogCallback;
 readonly P6CdInfo[] catalog;
 readonly Func<uint, ulong, byte[], int, bool> readCatalog;
 WaterboxHost host;
 bool faulted;
 public P6CdHost(P6CdInfo descriptor, Func<ulong, byte[], int, bool> provider)
  { info = descriptor; read = provider ?? throw new ArgumentNullException(nameof(provider)); callback = Read; catalogCallback = ReadCatalog; }
 public P6CdHost(P6CdInfo[] descriptors, Func<uint, ulong, byte[], int, bool> provider)
  : this(default, (_, _, _) => false)
 {
  if (descriptors is null || descriptors.Length < 1 || descriptors.Length > 16) throw new ArgumentException("CD catalog requires 1..16 descriptors", nameof(descriptors));
  catalog = (P6CdInfo[])descriptors.Clone(); readCatalog = provider ?? throw new ArgumentNullException(nameof(provider));
 }
 internal Delegate[] Callbacks(WaterboxHost owner)
 {
  if (host != null) throw new InvalidOperationException("A CD binding belongs to one sandbox");
  host = owner; return new Delegate[] { callback, catalogCallback }; // Fixed slots0/1 for every CD host, before BizInvoker.
 }
 int Read(ulong at, IntPtr destination, uint count) => CopyRead(0, at, destination, count, false);
 int ReadCatalog(uint disc, ulong at, IntPtr destination, uint count) => CopyRead(disc, at, destination, count, true);
 int CopyRead(uint disc, ulong at, IntPtr destination, uint count, bool indexed)
 {
  try {
   if (count > 2448 || faulted || (indexed && (catalog is null || disc >= catalog.Length))) return -P6.Io;
   var data = new byte[count];
   if (!(indexed ? readCatalog(disc, at, data, (int)count) : read(at, data, (int)count))) return -P6.Io;
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
  if (catalog is not null) return candidate.HasValue ? P6.BadArgument : BindCatalog(guest, rebind);
  var descriptor = candidate ?? info; return guest.Lib.p6w_cd_bind((IntPtr)(&descriptor), callback, rebind ? 1u : 0u);
 }
 public int BindCatalog(Puae6Guest guest, bool rebind, P6CdInfo[] candidate = null)
 {
  if (!ReferenceEquals(host, guest.Host)) return P6.BadArgument;
  if (!guest.IsSealed || catalog is null) return P6.BadState;
  using var active = guest.Host.EnterExit();
  var descriptors = candidate ?? catalog;
  fixed (P6CdInfo* p = descriptors) return guest.Lib.p6w_cd_catalog_bind((IntPtr)p, (uint)descriptors.Length, catalogCallback, rebind ? 1u : 0u);
 }
 public int Change(Puae6Guest guest, uint index, bool inserted)
 {
  if (!ReferenceEquals(host, guest.Host)) return P6.BadArgument;
  if (!guest.IsSealed || faulted) return P6.BadState;
  using var active = guest.Host.EnterExit();
  return guest.Lib.p6w_cd_change(index, inserted ? 1u : 0u);
 }
 public int Status(Puae6Guest guest, uint[] output)
 {
  if (!ReferenceEquals(host, guest.Host) || output is null) return P6.BadArgument;
  if (!guest.IsSealed || faulted) return P6.BadState;
  using var active = guest.Host.EnterExit();
  fixed (uint* p = output) return guest.Lib.p6w_cd_status((IntPtr)p, (uint)output.Length);
 }
 public int ReadAt(Puae6Guest guest, ulong offset, byte[] output)
 {
  if (!ReferenceEquals(host, guest.Host)) return P6.BadArgument;
  if (faulted) return P6.BadState;
  using var active = guest.Host.EnterExit();
  fixed (byte* p = output) return guest.Lib.p6w_cd_read_at(offset, (IntPtr)p, (uint)output.Length);
 }
 public int Activate(Puae6Guest guest)
 {
  if (!ReferenceEquals(host, guest.Host)) return P6.BadArgument;
  if (!guest.IsSealed || faulted) return P6.BadState;
  using var active = guest.Host.EnterExit();
  return guest.Lib.p6w_cd_activate();
 }
 public void Restore(Puae6Guest guest, BinaryReader input, StateIdentity expected)
 {
  if (!ReferenceEquals(host, guest.Host)) throw new ArgumentException("Wrong sandbox", nameof(guest));
  if (!guest.IsSealed) throw new InvalidOperationException("Seal the CD baseline before restoring");
  var payload = StateEnvelope.Read(input, expected); // Reject identity/checksum before touching sandbox.
  using var stream = new MemoryStream(payload, false); using var snapshot = new BinaryReader(stream);
  LoadSnapshot(guest, snapshot);
 }
 internal void LoadSnapshot(Puae6Guest guest, BinaryReader snapshot)
 {
  if (!ReferenceEquals(host, guest.Host) || !guest.IsSealed) throw new InvalidOperationException("Wrong or unsealed CD sandbox");
  using var active = guest.Host.EnterExit();
  faulted = true;
  guest.Host.LoadStateBinary(snapshot);
  if (Bind(guest, true) != P6.Ok) throw new InvalidDataException("Restored CD binding refused");
  faulted = false;
 }
}
