using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;

// Test-only host: the production guest and its snapshot format stay untouched.
internal static class SnapshotPoison
{
    [DllImport("libSystem.B.dylib")] static extern nint memset(nint destination, int value, nuint bytes);
    record Point(byte[] State, ulong Frames, ulong Hash);
    record Sample(string Line, string? Pcm, uint EvenRgb, uint OddRgb);
    static ulong[] probes = [];
    static Point Save(Puae6Machine m) => new(m.Save(), m.AudioFrames, m.AudioHash);
    static void Load(Puae6Machine m, Point p)
    { m.Load(p.State); m.AudioFrames = p.Frames; m.AudioHash = p.Hash; }
    static Sample[] Run(Puae6Machine m, int n, int count = 8) => Enumerable.Range(n + 1, count).Select(u => {
        string line = m.Unit((ulong)u, 3);
        uint Rgb(int i) => BitConverter.ToUInt32(m.ReadGuest(probes[i], 4)) & 0xFFFFFF;
        return new Sample(line, m.LastPcm, Rgb(0), Rgb(1));
    }).ToArray();
    static object Heaps(Puae6Machine m) => m.Usage().Select((h, i) => new {
        name = m.HeapNames[i], committed = h.Committed, dirty = h.Dirty, invisible = h.Invisible }).ToArray();
    static object Regions(Puae6Machine m, JsonElement regions)
    {
        var dirty = m.DirtyAddresses().ToHashSet();
        return regions.EnumerateArray().Select(r => {
            ulong start = r.GetProperty("address").GetUInt64(), size = r.GetProperty("size").GetUInt64();
            ulong first = start & ~4095UL, end = (start + size + 4095) & ~4095UL;
            var pages = Enumerable.Range(0, checked((int)((end - first) / 4096))).Select(i => first + (ulong)i * 4096).ToArray();
            var groups = pages.Select(p => p & ~16383UL).Distinct().ToArray();
            return new { symbol = r.GetProperty("symbol").GetString(), address = start, bytes = size,
                logical_pages = pages.Length, dirty_pages = pages.Count(dirty.Contains), groups_16k = groups.Length,
                dirty_groups_16k = groups.Count(g => Enumerable.Range(0, 4).Any(i => dirty.Contains(g + (ulong)i * 4096))) };
        }).ToArray();
    }
    static Puae6Machine Open(Native api, JsonElement c, Layout layout)
    {
        var m = new Puae6Machine(api, File.ReadAllBytes(c.GetProperty("guest").GetString()!), layout);
        try {
            m.MachineId = c.GetProperty("machine").GetUInt32();
            m.Start(File.ReadAllBytes(c.GetProperty("firmware").GetString()!), [], c.GetProperty("standard").GetUInt32(), 1, 946684800);
            return m;
        } catch { m.Dispose(); throw; }
    }
    static void Poison(Puae6Machine m, ulong address, int bytes, byte value)
    {
        byte[] left = m.ReadGuest(address - 64, 64), right = m.ReadGuest(address + (ulong)bytes, 64);
        // A native store is essential: managed stores can intercept Waterbox's dirty-page fault (docs/03).
        m.In(() => { memset((nint)address, value, (nuint)bytes); return 0; });
        Program.Require(m.ReadGuest(address, bytes).All(b => b == value), "the entire candidate was poisoned");
        Program.Require(left.SequenceEqual(m.ReadGuest(address - 64, 64)) && right.SequenceEqual(m.ReadGuest(address + (ulong)bytes, 64)), "poison did not touch the neighbours");
    }
    static int Main(string[] args)
    {
        try {
            using var config = JsonDocument.Parse(File.ReadAllText(args[0]));
            var c = config.RootElement;
            using var manifest = JsonDocument.Parse(File.ReadAllText(c.GetProperty("manifest").GetString()!));
            var l = manifest.RootElement.GetProperty("layout");
            Layout layout = new() { Sbrk = l.GetProperty("sbrk").GetUInt32(), Sealed = l.GetProperty("sealed").GetUInt32(),
                Invisible = l.GetProperty("invisible").GetUInt32(), Plain = l.GetProperty("plain").GetUInt32(), Mmap = l.GetProperty("mmap").GetUInt32() };
            using var api = new Native(c.GetProperty("runtime").GetString()!);
            using var m = Open(api, c, layout);
            var regions = c.GetProperty("regions");
            var pixels = regions.EnumerateArray().Single(r => r.GetProperty("symbol").GetString() == "pixels");
            ulong address = pixels.GetProperty("address").GetUInt64();
            int bytes = pixels.GetProperty("size").GetInt32();
            probes = c.GetProperty("probe_offsets").EnumerateArray().Select(o => address + o.GetUInt64()).ToArray();
            if (c.TryGetProperty("restore", out var restore)) {
                m.Load(File.ReadAllBytes(restore.GetString()!));
                var trace = Run(m, c.GetProperty("checkpoint").GetInt32());
                Console.WriteLine("RESULT " + JsonSerializer.Serialize(new { trace, digest = m.Digest() }));
                return 0;
            }
            Program.Require(address >= m.Ranges[0] && address + (ulong)bytes <= m.Ranges[0] + m.Ranges[1], "candidate inside guest ELF");
            // Inspect physical groups: no invisible/plain/RO page can share the candidate's groups.
            for (ulong g = address & ~16383UL; g < address + (ulong)bytes; g += 16384)
                for (int i = 0; i < 4; ++i)
                    Program.Require((m.PageInfo(checked((int)((g + (ulong)i * 4096 - m.Ranges[0]) / 4096))) & 0x7f) == 3, "candidate group is uniformly captured RW");
            var seal = new { heaps = Heaps(m), regions = Regions(m, regions), snapshot_bytes = m.Save().Length };
            Run(m, 0, 1);
            var first = new { heaps = Heaps(m), regions = Regions(m, regions), snapshot_bytes = m.Save().Length };
            Run(m, 1, 11);
            var results = new List<object>();
            foreach (int n in new[] { 12, 13 }) {
                var point = Save(m);
                string statePath = Path.Combine(c.GetProperty("directory").GetString()!, $"state-{n}.bin");
                File.WriteAllBytes(statePath, point.State);
                byte[] picture = m.ReadGuest(address, bytes);
                var trace = Run(m, n);
                string digest = m.Digest();
                var poisons = new List<object>();
                foreach (byte value in new byte[] { 0xA5, 0x5A }) {
                    Load(m, point);
                    Poison(m, address, bytes, value);
                    poisons.Add(new { value, trace = Run(m, n) });
                    Load(m, point);
                    Program.Require(picture.SequenceEqual(m.ReadGuest(address, bytes)), "captured snapshot repairs every byte of the candidate");
                    Program.Require(trace.SequenceEqual(Run(m, n)) && digest == m.Digest(), "captured restore repairs the exact future and full machine state");
                }
                using (var other = Open(api, c, layout)) {
                    other.Load(point.State);
                    Program.Require(picture.SequenceEqual(other.ReadGuest(address, bytes)), "a new sandbox restores the entire candidate");
                    Program.Require(trace.SequenceEqual(Run(other, n)) && digest == other.Digest(), "a new sandbox continues exactly");
                }
                var saveNs = new List<double>(); var loadNs = new List<double>();
                for (int i = 0; i < 5; ++i) {
                    long begin = Stopwatch.GetTimestamp(); var saved = m.Save(); long middle = Stopwatch.GetTimestamp();
                    m.Load(saved); long end = Stopwatch.GetTimestamp();
                    saveNs.Add((middle - begin) * 1e9 / Stopwatch.Frequency); loadNs.Add((end - middle) * 1e9 / Stopwatch.Frequency);
                }
                results.Add(new { checkpoint = n, trace, digest, poisons, snapshot_bytes = point.State.Length,
                    regions = Regions(m, regions), heaps = Heaps(m), save_ns = saveNs, load_ns = loadNs });
                Load(m, point); Run(m, n, 1);
            }
            Console.WriteLine("RESULT " + JsonSerializer.Serialize(new { seal, first, checkpoints = results }));
            return 0;
        } catch (Exception e) { Console.Error.WriteLine(e); return 1; }
    }
}
