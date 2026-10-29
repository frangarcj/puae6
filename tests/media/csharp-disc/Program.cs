using System.Text;
using System.Text.Json;
using BizHawk.Emulation.DiscSystem;
using Puae6;

internal static unsafe partial class Program
{
 static void Require(bool value) { if (!value) throw new Exception("CD source assertion failed"); }
 static Disc Mount(string path)
 {
  var job = new DiscMountJob(path); job.Run();
  return job.OUT_Disc ?? throw new Exception("DiscSystem failed to mount fixture");
 }
 static void Refused(byte[] output, Func<bool> read)
 {
  Array.Fill(output, (byte)0xcc); Require(!read()); Require(output.All(b => b == 0xcc));
 }
 static void Iso(string directory)
 {
  string path = Path.Combine(directory, "game.iso"), same = Path.Combine(directory, "same.iso"), changed = Path.Combine(directory, "tail.iso");
  var bytes = Enumerable.Range(0, 520 * 2048).Select(i => (byte)(i * 13)).ToArray();
  File.WriteAllBytes(path, bytes); File.WriteAllBytes(same, bytes);
  bytes[^1] ^= 1; File.WriteAllBytes(changed, bytes);
  using var disc = Mount(path); using var identical = Mount(same); using var tail = Mount(changed);
  var source = new Puae6DiscSource(disc); var output = new byte[2449];
  var descriptor = source.Descriptor(); Require(descriptor.Version == 1 && descriptor.Size == 2432 && descriptor.Bytes == 520UL * 2448);
  try { source.DescriptorV2(); throw new Exception("Invented typed ISO regions"); } catch (InvalidOperationException) { }
  Require(source.Tracks.Count == 1 && source.Tracks[0].Index0 == 0 && source.Leadout == 520);
  Require(source.ReadSector(0, 2048, output, 1) && output.AsSpan(1, 2048).SequenceEqual(bytes.AsSpan(0, 2048)));
  var direct = new DiscSectorReader(disc) { Policy = { DeinterleavedSubcode = true } };
  var raw = new byte[4896]; Require(direct.ReadLBA_2448(0, raw, 0) == 2448 && direct.ReadLBA_2448(1, raw, 2448) == 2448);
  Require(source.ReadAt(2439, output, 1, 2448) && output.AsSpan(1, 2448).SequenceEqual(raw.AsSpan(2439, 2448)));
  Require(source.ReadSector(0, 2352, output) && output.AsSpan(0, 2352).SequenceEqual(raw.AsSpan(0, 2352)));
  Require(source.ReadAt(source.Length, Array.Empty<byte>(), 0, 0));
  Refused(output, () => source.ReadAt(source.Length, output, 0, 1));
  Refused(output, () => source.ReadAt(ulong.MaxValue, output, 0, 1));
  Refused(output, () => source.ReadAt(0, output, 0, 2449));
  Refused(output, () => source.ReadAt(0, output, -1, 1));
  Refused(output, () => source.ReadSector(-1, 2448, output));
  Refused(output, () => source.ReadSector(520, 2448, output));
  Require(!source.Rebind(tail)); // Difference lies beyond OldHash's first 512 sectors.
  Require(source.ReadSector(519, 2048, output) && output[2047] != bytes[^1]);
  File.WriteAllBytes(Path.ChangeExtension(same, ".sbi"), new byte[] { 83, 66, 73, 0, 0, 2, 0, 2, 1, 0x23, 0x45 });
  using var subchannel = Mount(same);
  Require(!source.Rebind(subchannel)); // Same data/TOC; only the effective subchannel changed.
  subchannel.Dispose();
  Require(source.Rebind(identical)); disc.Dispose();
  Require(source.ReadSector(0, 2448, output) && output.AsSpan(0, 2448).SequenceEqual(raw.AsSpan(0, 2448)));
  var id = source.ContentId; id[0] ^= 255; Require(!id.SequenceEqual(source.ContentId));
  tail.Dispose(); using (var file = File.OpenWrite(changed)) file.SetLength(0);
  Require(!source.Rebind(tail)); // Failed candidate leaves the preceding binding usable.
  Require(source.ReadSector(0, 2048, output));
  identical.Dispose(); using (var file = File.OpenWrite(same)) file.SetLength(0);
  Refused(output, () => source.ReadAt(2439, output, 0, 2448));
 }
 static byte[] Wave(string path)
 {
  var payload = Enumerable.Range(0, 2352 * 4).Select(i => (byte)(i + i / 2352 * 17)).ToArray();
  using var file = File.Create(path); using var w = new BinaryWriter(file, Encoding.ASCII);
  w.Write(Encoding.ASCII.GetBytes("RIFF")); w.Write(36 + payload.Length); w.Write(Encoding.ASCII.GetBytes("WAVEfmt "));
  w.Write(16); w.Write((ushort)1); w.Write((ushort)2); w.Write(44100); w.Write(176400);
  w.Write((ushort)4); w.Write((ushort)16); w.Write(Encoding.ASCII.GetBytes("data")); w.Write(payload.Length); w.Write(payload);
  return payload;
 }
 static void Cue(string directory)
 {
  File.WriteAllBytes(Path.Combine(directory, "data.bin"), new byte[2048 * 4]);
  var payload = Wave(Path.Combine(directory, "audio.wav"));
  string prefix = "FILE \"data.bin\" BINARY\n TRACK 01 MODE1/2048\n INDEX 01 00:00:00\nFILE \"audio.wav\" WAVE\n TRACK 02 AUDIO\n";
  string path = Path.Combine(directory, "stored.cue"), other = Path.Combine(directory, "other.cue"), synthetic = Path.Combine(directory, "synthetic.cue");
  File.WriteAllText(path, prefix + " INDEX 00 00:00:00\n INDEX 01 00:00:02\n");
  File.WriteAllText(other, prefix + " INDEX 00 00:00:00\n INDEX 01 00:00:01\n");
  File.WriteAllText(synthetic, prefix + " PREGAP 00:00:03\n INDEX 01 00:00:00\n");
  using var disc = Mount(path); using var moved = Mount(other); using var gap = Mount(synthetic);
  var source = new Puae6DiscSource(disc); var track = source.Tracks[1]; var output = new byte[2448];
  Require(track.Index0 == 4 && track.Index1 == 6 && track.End == 8 && track.Offset == 4UL * 2448);
  Require(source.ReadSector(4, 2352, output) && output.AsSpan(0, 2352).SequenceEqual(payload.AsSpan(0, 2352)));
  Require(source.ReadSector(6, 2352, output) && output.AsSpan(0, 2352).SequenceEqual(payload.AsSpan(4704, 2352)));
  Refused(output, () => source.ReadSector(6, 2048, output));
  Require(!source.Rebind(moved) && source.ReadSector(6, 2352, output)); // Same length, changed TOC.
  var synthesized = new Puae6DiscSource(gap);
  Require(synthesized.Tracks[1].Index0 == 4 && synthesized.Tracks[1].Index1 == 7);
  Require(synthesized.ReadSector(4, 2352, output) && output.AsSpan(0, 2352).ToArray().All(b => b == 0));
  var mode2 = new byte[2352]; mode2[15] = 2; mode2[18] = 0x20;
  File.WriteAllBytes(Path.Combine(directory, "mode2.bin"), mode2);
  string modePath = Path.Combine(directory, "mode2.cue");
  File.WriteAllText(modePath, "FILE \"mode2.bin\" BINARY\n TRACK 01 MODE2/2352\n INDEX 01 00:00:00\n");
  using var modeDisc = Mount(modePath); var raw = new Puae6DiscSource(modeDisc);
  Require(raw.ReadSector(0, 2352, output) && output.AsSpan(0, 2352).SequenceEqual(mode2));
  Refused(output, () => raw.ReadSector(0, 2048, output));
 }
 // Actual decompressed unit source for the CHD mount seam; no native decoder API.
 sealed class UnitsBlob : IBlob
 {
  readonly byte[] bytes; public int Reads, Closes;
  public UnitsBlob(byte[] data) { bytes=data; }
  public int Read(long at, byte[] output, int offset, int count) {
   if(Closes!=0||at<0||at>bytes.LongLength-count) throw new IOException("Unit range");
   ++Reads; Buffer.BlockCopy(bytes,(int)at,output,offset,count); return count;
  }
  public void Dispose() { Closes=1; }
 }
 static void ChdUnits(string path, bool regional = false)
 {
  using var input=JsonDocument.Parse(File.ReadAllText(path)); var rows=input.RootElement.GetProperty("tracks");
  var chd=new CHD_Format.CHDFile { Header=new LibChd.chd_header { unitbytes=2448, hunkbytes=19584 } };
  uint[] sizes={2048,2352,2336,2048,2324,2336,2352,2352};
  foreach(var row in rows.EnumerateArray()) {
   var a=row.EnumerateArray().Select(v=>v.GetUInt32()).ToArray();
   chd.CdMetadatas.Add(new() { Track=(uint)chd.CdMetadatas.Count+1, TrackType=(LibChd.chd_track_type)a[0],
    SubType=(LibChd.chd_sub_type)a[1], Frames=a[2], Pregap=a[3], PregapTrackType=(LibChd.chd_track_type)a[4],
    PregapSubType=(LibChd.chd_sub_type)a[5], PregapInChd=a[6]!=0, PostGap=a[7], ExtraFrames=(0-a[2])&3,
    ByteSwapAudio=true, SectorSize=sizes[a[0]], SubSize=a[1]==2?0u:96u });
  }
  var original=chd.CdMetadatas.Select(t=>(t.PregapTrackType,t.PregapSubType)).ToArray();
  var blob=new UnitsBlob(File.ReadAllBytes(Path.ChangeExtension(path,"units")));
  var policy=new DiscMountPolicy { CUE_PregapMode2_As_XA_Form2=input.RootElement.GetProperty("xaGap").GetBoolean() };
  using(var disc=CHD_Format.LoadCHDToDisc(chd,blob,policy)) {
   Require(blob.Reads==0&&original.SequenceEqual(chd.CdMetadatas.Select(t=>(t.PregapTrackType,t.PregapSubType))));
   var immutable=disc.Regions.ToArray(); chd.CdMetadatas[0].TrackType=LibChd.chd_track_type.CD_TRACK_MODE2;
   chd.CdMetadatas[0].Pregap=999; Require(immutable.SequenceEqual(disc.Regions));
   try { ((IList<DiscRegionInfo>)disc.Regions).Clear(); throw new Exception("Mutable regions"); }
   catch(NotSupportedException) { }
   Require(typeof(Disc).GetProperty(nameof(Disc.Regions)).GetSetMethod(true).IsAssembly);
   foreach(var r in disc.Regions) Console.WriteLine($"region {r.Start} {r.End} {r.Track} {r.Index} {r.Mode} {r.Control} {(r.Synthetic?1:0)} {(uint)r.Type} {(uint)r.SubType}");
   disc.SynthProvider=new ArraySectorSynthProvider { Sectors=disc._Sectors, FirstLBA=-150 }; // Same provider as DiscMountJob.
   if (regional) RegionalSource(disc,path);
   var reader=new DiscSectorReader(disc); var output=new byte[2464];
   using var rendered=File.Create(Path.ChangeExtension(path,"actual"));
   for(int lba=0;lba<disc._Sectors.Count-150;++lba) {
    Array.Fill(output,(byte)0xa5); Require(reader.ReadLBA_2448(lba,output,8)==2448);
    Require(output.Take(8).Concat(output.Skip(2456)).All(v=>v==0xa5));
    rendered.Write(output,8,2448);
    Console.WriteLine($"sector {lba} {CdHash(output.AsSpan(8,2448).ToArray())}");
   }
  }
  Require(blob.Closes==1);
  string mds=Path.ChangeExtension(path,"mds");
  if(File.Exists(mds)) {
   using var body=new UnitsBlob(File.ReadAllBytes(mds)); var output=Enumerable.Repeat((byte)0xa5,2368).ToArray();
   var synth=new BizHawk.Emulation.DiscSystem.CUE.SS_Mode2_Form2_2328 { Blob=body, Policy=policy };
   synth.Synth(new SectorSynthJob { LBA=4350, DestBuffer2448=output, DestOffset=8, Parts=ESectorSynthPart.User2352 });
   Require(output.Take(8).Concat(output.Skip(2360)).All(v=>v==0xa5));
   Console.WriteLine("mds "+CdHash(output.AsSpan(8,2352).ToArray()));
  }
 }
 static void FinalizeChd(Disc disc) {
  disc.SynthProvider=new ArraySectorSynthProvider { Sectors=disc._Sectors, FirstLBA=-150 };
  foreach(var session in disc.Sessions.Skip(1)) {
   var toc=new Synthesize_DiscTOC_From_RawTOCEntries_Job(session.RawTOCEntries); toc.Run(); session.TOC=toc.Result;
   new Synthesize_DiscTracks_From_DiscTOC_Job(disc,session).Run();
  }
 }
 static void RegionalSource(Disc disc,string path) {
  FinalizeChd(disc);
  var source=new Puae6DiscSource(disc);
  var wire=source.DescriptorV2();
  File.WriteAllBytes(Path.ChangeExtension(path,"descriptor"),new ReadOnlySpan<byte>(&wire,sizeof(P6CdInfoV2)).ToArray());
  try { source.Descriptor(); throw new Exception("Flattened typed regions into V1"); } catch(InvalidOperationException) { }
  var regions=disc.Regions.ToArray(); var identity=source.ContentId;
  var direct=new DiscSectorReader(disc) { Policy={ DeinterleavedSubcode=true, DeterministicClearBuffer=true } };
  var actual=new byte[2448]; var output=new byte[2464];
  for(int lba=0;lba<source.Leadout;++lba) {
   Require(direct.ReadLBA_2448(lba,actual,0)==2448);
   Array.Fill(output,(byte)0xa5); Require(source.ReadAt((ulong)lba*2448,output,8,2448));
   Require(output.AsSpan(8,2448).SequenceEqual(actual)&&output.Take(8).Concat(output.Skip(2456)).All(v=>v==0xa5));
   var region=regions.Single(r=>lba>=r.Start&&lba<r.End);
   if(region.Mode==1&&actual[15]==1) {
    Require(source.ReadSector(lba,2048,output,8)&&output.AsSpan(8,2048).SequenceEqual(actual.AsSpan(16,2048)));
   } else Refused(output,()=>source.ReadSector(lba,2048,output,8));
  }
  DiscRegionInfo Change(DiscRegionInfo r,int? start=null,int? end=null,int? track=null,int? index=null,LibChd.chd_track_type? type=null)
   =>new(start??r.Start,end??r.End,track??r.Track,index??r.Index,type??r.Type,r.SubType,r.Synthetic);
  int pg=Array.FindIndex(regions,r=>r.Index==0), program=Array.FindIndex(regions,r=>r.Index==1), post=Array.FindIndex(regions,r=>r.Index==2);
  var split=regions.ToList(); split[pg]=Change(split[pg],end:split[pg].Start+1);
  var remainder=regions[pg]; split.Insert(pg+1,new(split[pg].End,remainder.End,remainder.Track,0,remainder.Type,LibChd.chd_sub_type.CD_SUB_NONE,!remainder.Synthetic));
  disc.Regions=Array.AsReadOnly(split.ToArray()); Require(source.Rebind(disc)&&source.ContentId.SequenceEqual(identity));
  for(int bad=0;bad<11;++bad) {
   var rows=regions.ToList();
   if(bad==0) rows[0]=Change(rows[0],start:1);
   if(bad==1) rows[0]=Change(rows[0],end:rows[0].End+1);
   if(bad==2) rows[program]=Change(rows[program],index:2);
   if(bad==3) { rows[program]=Change(rows[program],end:rows[program].Start+1); rows.Insert(program+1,Change(regions[program],start:rows[program].End)); }
   if(bad==4) { rows[pg]=Change(rows[pg],end:rows[pg].Start+1); rows.Insert(pg+1,Change(regions[pg],start:rows[pg].End,type:LibChd.chd_track_type.CD_TRACK_MODE1)); }
   if(bad==5) rows[post]=Change(rows[post],type:LibChd.chd_track_type.CD_TRACK_MODE1);
   if(bad==6) rows[^1]=Change(rows[^1],end:source.Leadout+1);
   if(bad==7) rows[0]=Change(rows[0],track:2);
   if(bad==8) rows[0]=Change(rows[0],type:(LibChd.chd_track_type)99);
   if(bad==9) rows[pg]=Change(rows[pg],index:3);
   if(bad==10) rows[program]=Change(rows[program],type:LibChd.chd_track_type.CD_TRACK_MODE1);
   disc.Regions=Array.AsReadOnly(rows.ToArray());
   Require(!source.Rebind(disc));
   try { _=new Puae6DiscSource(disc); throw new Exception("Accepted contradictory regions"); } catch(InvalidDataException) { }
   Require(source.ContentId.SequenceEqual(identity)&&source.ReadSector(0,2448,output));
  }
  // Same immutable sector provider, different effective pregap metadata: hash it too.
  disc.Regions=Array.AsReadOnly(regions.Select(r=>r.Track==regions[pg].Track&&r.Index==0?Change(r,type:LibChd.chd_track_type.CD_TRACK_MODE2_RAW):r).ToArray());
  Require(!new Puae6DiscSource(disc).ContentId.SequenceEqual(identity)&&!source.Rebind(disc));
  disc.Regions=Array.AsReadOnly(regions); Require(source.Rebind(disc));
  Require(source.ContentId.SequenceEqual(identity)&&source.ReadSector(0,2448,output));
 }
 static Disc MountedChdHost(string path, DiscMountPolicy policy) {
  var disc=CHD_Format.LoadPuae6Chd(path,policy);
  try {
   FinalizeChd(disc);
   return disc; // The existing DiscMountJob finalization jobs, without a public selector.
  } catch { disc.Dispose(); throw; }
 }
 static void ChdHost(string path) {
  var policy=new DiscMountPolicy();
  using(var blob=new CHD_Format.Puae6ChdBlob(path)) {
   Require(blob.Geometry.UnitBytes==2448&&blob.Geometry.HunkBytes==19584&&blob.Geometry.Bytes==40*2448&&blob.Geometry.Tracks==1);
   Require(blob.Metadata(0).TrackType==LibChd.chd_track_type.CD_TRACK_AUDIO);
   var output=Enumerable.Repeat((byte)0xa5,2464).ToArray();
   Require(blob.Read(19577,output,8,2448)==2448&&output.Take(8).Concat(output.Skip(2456)).All(v=>v==0xa5));
   File.WriteAllBytes(path+".units",output.AsSpan(8,2448).ToArray());
   Array.Fill(output,(byte)0xa5);
   try { blob.Read(40*2448-3,output,8,4); throw new Exception("Accepted CHD EOF"); } catch(IOException) { }
   Require(output.All(v=>v==0xa5)); blob.Dispose();
   try { blob.Read(0,output,8,4); throw new Exception("Read disposed CHD"); } catch(IOException) { }
  }
  using var disc=MountedChdHost(path,policy);
  var source=new Puae6DiscSource(disc); var canonical=new byte[(int)source.Length];
  var descriptor=source.DescriptorV2();
  File.WriteAllBytes(path+".descriptor",new ReadOnlySpan<byte>(&descriptor,sizeof(P6CdInfoV2)).ToArray());
  for(int at=0;at<canonical.Length;at+=2448) Require(source.ReadAt((ulong)at,canonical,at,2448));
  File.WriteAllBytes(path+".actual",canonical);
  Console.WriteLine(Convert.ToHexString(source.ContentId));
  foreach(var r in disc.Regions) Console.WriteLine($"{r.Start} {r.End} {r.Track} {r.Index} {r.Mode} {r.Control} {r.Synthetic} {(uint)r.Type} {(uint)r.SubType}");
  string copy=path+".copy"; File.Copy(path,copy,true);
  using var replacement=MountedChdHost(copy,policy);
  Require(source.Rebind(replacement)); disc.Dispose();
  var probe=new byte[2449]; Require(source.ReadAt(19577,probe,1,2448)&&probe.AsSpan(1,2448).SequenceEqual(canonical.AsSpan(19577,2448)));
  using(var file=new FileStream(copy,FileMode.Open,FileAccess.Write,FileShare.ReadWrite)) file.SetLength(0);
  Refused(probe,()=>source.ReadAt(0,probe,1,2448));
  replacement.Dispose(); Refused(probe,()=>source.ReadAt(0,probe,1,2448));
 }
 static int NrgHandles(string path)
 {
  path=Path.GetFullPath(path);
  if(OperatingSystem.IsMacOS()) {
   using var query=System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("/usr/sbin/lsof",$"-a -p {Environment.ProcessId} -Fn") { UseShellExecute=false,RedirectStandardOutput=true });
   string files=query.StandardOutput.ReadToEnd(); query.WaitForExit(); Require(query.ExitCode==0);
   return files.Split('\n').Count(line=>line=="n"+path);
  }
  int count=0;
  foreach(string entry in Directory.GetFiles("/dev/fd"))
   try { if(File.ResolveLinkTarget(entry,true)?.FullName==path) ++count; } catch(IOException) { }
  return count;
 }
 sealed class NrgMetadataStream(byte[] directory, long start, string fault) : Stream
 {
  long position; int reads; public bool Closed;
  public override bool CanRead => true; public override bool CanSeek => true; public override bool CanWrite => false;
  public override long Length => start + directory.Length;
  public override long Position { get => position; set => position=value; }
  public override int Read(byte[] output,int offset,int count)
  {
   if(position<start||position>Length) throw new InvalidOperationException("NRG parser read payload");
   if(++reads==2) { if(fault=="io") throw new IOException("Injected metadata IO error"); if(fault=="short") return 0; }
   int take=(int)Math.Min(count,Length-position); Buffer.BlockCopy(directory,(int)(position-start),output,offset,take); position+=take; return take;
  }
  public override long Seek(long offset,SeekOrigin origin) => Position=(origin==SeekOrigin.Begin?0:origin==SeekOrigin.Current?Position:Length)+offset;
  public override void Flush() { }
  public override void SetLength(long value) => throw new NotSupportedException();
  public override void Write(byte[] buffer,int offset,int count) => throw new NotSupportedException();
  protected override void Dispose(bool disposing) { Closed=true; base.Dispose(disposing); }
 }
 static void NrgMetadata(string[] args)
 {
  using var input=new NrgMetadataStream(File.ReadAllBytes(args[1]),long.Parse(args[2]),args.Length>3?args[3]:"");
  NRG_Format.NRGFile parsed=null;
  try { parsed=args.Length>3&&args[3]=="legacy"?NRG_Format.ParseFrom(input):NRG_Format.ParsePuae6Nrg(input); }
  catch(Exception e) when(e is IOException or NRG_Format.NRGParseException or NotSupportedException)
   { Require(parsed==null&&input.Closed); Console.WriteLine("rejected "+e.GetType().Name); return; }
  Require(input.Closed);
  if(parsed.TAOTrackInfos.Count>1) {
   var table=new NRG_Format.Puae6DaoLayout(parsed); Require(table.Canonical150);
   foreach(var t in table.Sessions) Console.WriteLine($"global {t.FirstTrack} {t.LastTrack} {t.Format} {t.Cue0} {t.A2}");
   foreach(var t in table.Tracks) Console.WriteLine($"extent {t.Number} {t.Offset1} {t.EndOffset} {t.Stride} {t.Mode} {t.Index0} {t.Index1} {t.End}");
   return;
  }
  var first=parsed.DAOTrackInfos[0]; var last=parsed.Cues.Last().TrackIndices.Single(c=>c.Track.BCDValue==0xaa);
  Console.WriteLine($"nrg {(parsed.FileID=="NER5"?2:1)} {parsed.DAOTrackInfos.Sum(d=>d.Tracks.Count)} {parsed.FileOffset} {parsed.Cues[0].TrackIndices.Single(c=>c.Track.BCDValue==0).LBA} {last.LBA} {first.DiskType} {first.SessionHint}");
  for(int group=0;group<parsed.DAOTrackInfos.Count;++group) {
   var dao=parsed.DAOTrackInfos[group]; var cues=parsed.Cues[group].TrackIndices;
   Console.WriteLine($"session {group+1} {dao.FirstTrack} {dao.LastTrack} {dao.DiskType} {cues.Single(c=>c.Track.BCDValue==0).LBA} {cues.Single(c=>c.Track.BCDValue==0xaa).LBA} {dao.SessionHint} {Convert.ToHexString(dao.CatalogRaw).ToLowerInvariant()}");
  }
  for(int group=0;group<parsed.DAOTrackInfos.Count;++group) {
   var dao=parsed.DAOTrackInfos[group]; var cues=parsed.Cues[group].TrackIndices;
   for(int n=0;n<dao.Tracks.Count;++n) {
    var t=dao.Tracks[n]; var q=cues.Single(c=>c.Track.DecimalValue==dao.FirstTrack+n&&c.Index.BCDValue==1);
    var zero=cues.SingleOrDefault(c=>c.Track.DecimalValue==dao.FirstTrack+n&&c.Index.BCDValue==0);
    Console.WriteLine($"track {t.PregapFileOffset} {t.TrackStartFileOffset} {t.TrackEndFileOffset} {t.SectorSize} {t.Mode} {zero?.LBA??0} {q.LBA} {(zero==null?0:1)} {q.ADRControl&15} {q.ADRControl>>4}");
   }
  }
  foreach(var dao in parsed.DAOTrackInfos) foreach(var t in dao.Tracks) Console.WriteLine("isrc "+Convert.ToHexString(t.IsrcRaw).ToLowerInvariant());
 }
 sealed class NrgCountBlob(IBlob original) : IBlob
 {
  public int Calls,Fault,FailAt;
  public int Read(long at,byte[] output,int offset,int count) {
   ++Calls; if(Fault==1&&(FailAt==0||Calls==FailAt)) throw new EndOfStreamException("Injected backing EOF"); if(Fault==2&&(FailAt==0||Calls==FailAt)) throw new IOException("Injected sparse IO");
   return original.Read(at,output,offset,count);
  }
  public void Dispose() { }
 }
 static void NrgSparse(string[] args)
 {
  string path=args[1]; int before=NrgHandles(path); Disc disc=null;
  try {
   disc=DiscMountJob.MountPuae6Nrg(path);
   if(args[0]=="nrg-sparse-refuse") throw new Exception("Accepted unsupported sparse NRG");
   var layout=disc.Puae6NrgDao; Require(layout!=null&&layout.Sessions.Count>=2&&disc.Sessions.Count==layout.Sessions.Count+1&&disc._Sectors.Count==0);
   try { ((IList<NRG_Format.Puae6DaoSession>)layout.Sessions)[0]=default; throw new Exception("Mutable mount table"); } catch(NotSupportedException) { }
   int number=0;
   foreach(var s in layout.Sessions) {
    var toc=disc.Sessions[++number]; Require(toc.Number==number&&toc.TOC.FirstRecordedTrackNumber==s.FirstTrack&&toc.TOC.LastRecordedTrackNumber==s.LastTrack&&toc.LeadoutLBA==s.A2);
    Console.WriteLine($"session {number} {s.FirstTrack} {s.LastTrack} {s.Format} {s.Cue0} {s.A2} {s.SessionHint} {s.CatalogHex}");
    Require(toc.Tracks.Skip(1).Take(toc.InformationTrackCount).Select(t=>t.Number).SequenceEqual(Enumerable.Range(s.FirstTrack,s.LastTrack-s.FirstTrack+1)));
   }
   var field=disc.SynthProvider.GetType().GetField("blob",System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance);
   var counter=new NrgCountBlob((IBlob)field.GetValue(disc.SynthProvider)); field.SetValue(disc.SynthProvider,counter);
   var reader=new DiscSectorReader(disc); var output=new byte[2464]; using var wire=new MemoryStream();
   foreach(var t in layout.Tracks) {
    Console.WriteLine($"track {t.Number} {t.Offset0} {t.Offset1} {t.EndOffset} {t.Stride} {t.Mode} {t.Index0} {t.Index1} {t.End} {t.Adr} {t.Control} {t.IsrcHex}");
    for(int lba=t.Index0;lba<t.End;++lba) {
     Array.Fill(output,(byte)0xcc); Require(reader.ReadLBA_2448(lba,output,8)==2448);
     Require(output.Take(8).Concat(output.Skip(2456)).All(b=>b==0xcc)); wire.Write(output,8,2448);
    }
   }
   File.WriteAllBytes(path+".actual",wire.ToArray()); Require(NrgHandles(path)==before+1);
   void absent(int lba) { int calls=counter.Calls; Array.Fill(output,(byte)0xcc); Require(reader.ReadLBA_2448(lba,output,8)==0&&counter.Calls==calls&&output.All(b=>b==0xcc)); }
   absent(layout.Tracks[0].Index0-1);
   foreach(var s in layout.Sessions) { absent(s.A2); }
   if(!layout.Canonical150) absent(layout.Sessions[1].Cue0); absent(layout.Tracks.Last().End+1);
   if(args.Length>2) {
    foreach(int fault in new[]{1,2}) {
     counter.Fault=fault; bool refused=false;
     try { reader.ReadLBA_2448(layout.Tracks[0].Index0,output,8); } catch(IOException) { refused=true; }
     Require(refused); counter.Fault=0; Require(reader.ReadLBA_2448(layout.Tracks[0].Index0,output,8)==2448);
    }
    Require(reader.ReadLBA_2448(layout.Tracks.Last().Index1,output,8)==2448);
    using(var file=new FileStream(path,FileMode.Open,FileAccess.Write,FileShare.ReadWrite)) file.SetLength(0);
    bool shortRead=false; try { reader.ReadLBA_2448(layout.Tracks[0].Index0,output,8); } catch(IOException) { shortRead=true; } Require(shortRead);
   }
   disc.Dispose(); Require(disc.IsDisposed&&NrgHandles(path)==before); absent(layout.Tracks[0].Index0); disc.Dispose();
  }
  catch(Exception e) when(args[0]=="nrg-sparse-refuse"&&e is IOException or InvalidDataException or BizHawk.Emulation.DiscSystem.SBI.SBIParseException or NRG_Format.NRGParseException or NotSupportedException) { Console.WriteLine("rejected "+e.GetType().Name); }
  finally { disc?.Dispose(); }
  Require(NrgHandles(path)==before);
 }
 static void NrgSbi(string[] args)
 {
  string path=args[1]; int before=NrgHandles(path);
  using(var disc=DiscMountJob.MountPuae6Nrg(path)) {
   var layout=disc.Puae6NrgDao; var original=disc.SynthProvider;
   var reader=new DiscSectorReader(disc); var output=new byte[2464];
   if(args[0]=="nrg-sbi-faults") {
    var field=original.GetType().GetField("blob",System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance);
    var counter=new NrgCountBlob((IBlob)field.GetValue(original)); field.SetValue(original,counter);
    object memo=new(); disc.Memos["sbi"]=memo;
    BizHawk.Emulation.DiscSystem.SBI.SubQPatchData Data(params int[] abas) {
     var data=new BizHawk.Emulation.DiscSystem.SBI.SubQPatchData { subq=Enumerable.Repeat((short)-1,abas.Length*12).ToArray() };
     foreach(int aba in abas) data.ABAs.Add(aba); data.subq[0]=0x99; return data;
    }
    var bad=new[]{Data(150,161),Data(150,int.MinValue),Data(150)};
    bad[2].subq[11]=256;
    var shortData=Data(150); shortData.subq=new short[11];
    foreach(var data in bad.Append(shortData)) {
     int calls=counter.Calls;
     try { new ApplySBIJob().Run(disc,data,false); throw new Exception("Accepted malformed SBI"); }
     catch(Exception e) when(e is InvalidDataException or OverflowException) { }
     Require(counter.Calls==calls&&ReferenceEquals(original,disc.SynthProvider)&&ReferenceEquals(memo,disc.Memos["sbi"]));
    }
    Require(reader.ReadLBA_SubQ(0,output,0)==12); var q=output[..12];
    counter.Fault=2; counter.FailAt=counter.Calls+2;
    try { new ApplySBIJob().Run(disc,Data(150,182),false); throw new Exception("Accepted SBI backing failure"); } catch(IOException) { }
    Require(counter.Calls==counter.FailAt&&ReferenceEquals(original,disc.SynthProvider)&&ReferenceEquals(memo,disc.Memos["sbi"]));
    counter.Fault=0; Require(reader.ReadLBA_SubQ(0,output,0)==12&&output.AsSpan(0,12).SequenceEqual(q));
    new ApplySBIJob().Run(disc,Data(150),false);
    Require(!ReferenceEquals(original,disc.SynthProvider)); int done=counter.Calls;
    disc.Dispose(); Require(disc.IsDisposed&&NrgHandles(path)==before);
    foreach(int lba in new[]{0,1}) Require(disc.SynthProvider.Get(lba)==null);
    Require(counter.Calls==done); disc.Dispose();
   } else {
    if(args.Length>2) { var load=new BizHawk.Emulation.DiscSystem.SBI.LoadSBIJob(args[2]); load.Run(); new ApplySBIJob().Run(disc,load.OUT_Data,args[3]=="true"); }
    using var wire=new MemoryStream(); using var interleaved=new MemoryStream();
    foreach(var t in layout.Tracks) for(int lba=t.Index0;lba<t.End;++lba) {
     foreach(bool deinterleaved in new[]{true,false}) {
      reader.Policy.DeinterleavedSubcode=deinterleaved; Array.Fill(output,(byte)0xcc);
      Require(reader.ReadLBA_2448(lba,output,8)==2448&&output.Take(8).Concat(output.Skip(2456)).All(b=>b==0xcc));
      (deinterleaved?wire:interleaved).Write(output,8,2448);
     }
    }
    File.WriteAllBytes(path+".actual",wire.ToArray()); File.WriteAllBytes(path+".interleaved",interleaved.ToArray());
   }
  }
  Require(NrgHandles(path)==before); Console.WriteLine("SBI provider PASS");
 }
 static void NrgNormalized(string[] args)
 {
  string path=args[1]; int before=NrgHandles(path);
  using(var disc=DiscMountJob.MountPuae6Nrg(path)) {
   var source=new Puae6DiscSource(disc); var id=source.ContentId; var layout=disc.Puae6NrgDao;
   Require(source.SessionCount==layout.Sessions.Count);
   try { source.Descriptor(); throw new Exception("Multisession accepted V1"); } catch(InvalidOperationException) { }
   try { source.DescriptorV2(); throw new Exception("Multisession accepted V2"); } catch(InvalidOperationException) { }
   var descriptor=source.DescriptorV3(); File.WriteAllBytes(path+".descriptor",new ReadOnlySpan<byte>(&descriptor,sizeof(P6CdInfoV3)).ToArray());
   Console.WriteLine(Convert.ToHexString(id));
   var direct=new DiscSectorReader(disc); var output=new byte[2464]; using var wire=new MemoryStream();
   foreach(var t in layout.Tracks) for(int lba=t.Index0;lba<t.End;++lba) {
    Array.Fill(output,(byte)0xcc);
    Require(lba<0?direct.ReadLBA_2448(lba,output,8)==2448:source.ReadAt((ulong)lba*2448,output,8,2448));
    Require(output.Take(8).Concat(output.Skip(2456)).All(b=>b==0xcc)); wire.Write(output,8,2448);
   }
   File.WriteAllBytes(path+".actual",wire.ToArray());
   var field=disc.SynthProvider.GetType().GetField("blob",System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance);
   var counter=new NrgCountBlob((IBlob)field.GetValue(disc.SynthProvider)); field.SetValue(disc.SynthProvider,counter);
   int calls=counter.Calls; int hole=layout.Canonical150?layout.Sessions[0].A2:11, sample=layout.Tracks.Last().Index1;
   Refused(output,()=>source.ReadAt((ulong)hole*2448,output,8,2448));
   Refused(output,()=>source.ReadAt((ulong)hole*2448-1,output,8,2));
   Refused(output,()=>source.ReadSector(hole,2448,output,8));
   Require(counter.Calls==calls&&source.ReadAt(source.Length,Array.Empty<byte>(),0,0));
   if(args.Length>2) {
    string other=args[2]; int owners=NrgHandles(other);
    using(var replacement=DiscMountJob.MountPuae6Nrg(other)) {
     if(args[3]=="fault") {
      var f=replacement.SynthProvider.GetType().GetField("blob",System.Reflection.BindingFlags.NonPublic|System.Reflection.BindingFlags.Instance);
      f.SetValue(replacement.SynthProvider,new NrgCountBlob((IBlob)f.GetValue(replacement.SynthProvider)) { Fault=2 });
     }
     bool same=args[3]=="same"; Require(source.Rebind(replacement)==same&&source.ContentId.SequenceEqual(id));
     Require(source.ReadSector(sample,2448,output,8));
     if(same) { disc.Dispose(); Require(source.ReadSector(sample,2448,output,8)); replacement.Dispose(); Refused(output,()=>source.ReadAt(0,output,8,2448)); }
     else { replacement.Dispose(); Require(!source.Rebind(replacement)&&source.ReadSector(sample,2448,output,8)); }
    }
    Require(NrgHandles(other)==owners);
   } else {
    counter.Fault=2; Refused(output,()=>source.ReadAt(2439,output,8,2448)); counter.Fault=0;
    Require(source.ReadSector(0,2448,output,8)); disc.Dispose(); calls=counter.Calls;
    Refused(output,()=>source.ReadAt(0,output,8,2448)); Refused(output,()=>source.ReadSector(sample,2448,output,8)); Require(counter.Calls==calls);
   }
  }
  Require(NrgHandles(path)==before);
 }
 static void NrgTao(string[] args)
 {
  string mode=args[0],path=args[1]; int before=NrgHandles(path);
  try {
   using var disc=mode.Contains("default")?Mount(path):DiscMountJob.MountPuae6Nrg(path);
   if(mode.EndsWith("refuse")) throw new Exception("Accepted unsupported/malformed NRG");
   var source=new Puae6DiscSource(disc); var identity=source.ContentId;
   Console.WriteLine(Convert.ToHexString(identity));
   for(int n=1;n<=disc.Session1.InformationTrackCount;++n) Console.WriteLine($"track {n} {disc.Session1.Tracks[n].LBA}");
   Console.WriteLine($"leadout {source.Leadout}");
   if(disc.Regions.Count!=0) { var descriptor=source.DescriptorV2(); File.WriteAllBytes(path+".descriptor",new ReadOnlySpan<byte>(&descriptor,sizeof(P6CdInfoV2)).ToArray()); }
   var wire=new byte[source.Length];
   for(int at=0;at<wire.Length;at+=2448) Require(source.ReadAt((ulong)at,wire,at,2448));
   File.WriteAllBytes(path+".actual",wire); Require(NrgHandles(path)==before+1);
   var output=new byte[2464]; Refused(output,()=>source.ReadAt(source.Length,output,8,1));
   for(int at=2441;at+2448<wire.Length;at+=2448) {
    Array.Fill(output,(byte)0xcc); Require(source.ReadAt((ulong)at,output,8,2448)&&output.AsSpan(8,2448).SequenceEqual(wire.AsSpan(at,2448)));
    Require(output.Take(8).Concat(output.Skip(2456)).All(b=>b==0xcc));
   }
   if(args.Length>2) {
    using var other=DiscMountJob.MountPuae6Nrg(args[2]); bool accepted=args[3]=="same";
    Require(source.Rebind(other)==accepted&&source.ContentId.SequenceEqual(identity));
    Require(source.ReadAt(0,output,8,2448)&&output.AsSpan(8,2448).SequenceEqual(wire.AsSpan(0,2448)));
   }
  }
  catch(Exception e) when(mode.EndsWith("refuse")&&e is IOException or NRG_Format.NRGParseException or NotSupportedException) { Console.WriteLine("rejected "+e.GetType().Name); }
  Require(NrgHandles(path)==before);
 }
 static void Main(string[] args) {
  if(args.Length>5&&args[5].StartsWith("cdtv")) { CdtvWaterbox(args); return; }
  if(args.Length>5&&args[5].StartsWith("idemixed")) { IdeMixedWaterbox(args); return; }
  if(args.Length>=3&&args[0]=="nrg-metadata") { NrgMetadata(args); return; }
  if(args.Length>=2&&args[0].StartsWith("nrg-sbi")) { NrgSbi(args); return; }

  if(args.Length>=2&&args[0]=="nrg-normalized") { NrgNormalized(args); return; }
  if(args.Length>=2&&args[0].StartsWith("nrg-sparse")) { NrgSparse(args); return; }
  if(args.Length>=2&&args[0].StartsWith("nrg-")) { NrgTao(args); return; }
  if(args.Length==2&&args[0]=="chd-host") { ChdHost(args[1]); return; }
  if(args.Length==2&&args[0]=="chd-units") { ChdUnits(args[1]); return; }
  if(args.Length==2&&args[0]=="chd-regional") { ChdUnits(args[1],true); return; }
  if (args.Length > 1) { if (args[5].StartsWith("cd32")) Cd32Waterbox(args); else Waterbox(args); return; }
  Iso(args[0]); Cue(args[0]); Console.WriteLine("DiscSystem ISO/CUE offset, identity and rebind PASS");
 }
}
