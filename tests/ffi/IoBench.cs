// T34: equivalence and cost of the two parts of the core's unit that are not the engine and not the sandbox: the questions asked of the controller (BuildInput) and the
// picture copy (CopyOut). Each is run next to the form it replaced (kept here as the reference) on the same data:
//   * the controller names: Controllers.KeyButtons, JoystickButtons and Port1Mouse* are the strings that the interpolation made at every unit;
//   * PixelCopy.XrgbToArgb is `target[i] = source[i] | 0xFF000000` for every length, offset and bit pattern.
// Then the time per unit of both forms and the managed bytes they allocate. The clock is read here, around the calls; nothing here steps a machine.
//   Ffi.dll iobench --out FILE [--iterations N]
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;

using Puae6;

internal static unsafe partial class Program
{
	static void IoBench(Dictionary<string, string> options)
	{
		var checks = new List<string>();
		void Same(bool ok, string what) { Require(ok, what); checks.Add(what); }

		// ---- the names ----
		bool names = Controllers.KeyButtons.Length == Controllers.KeyNames.Length;
		for (int code = 0; code < Controllers.KeyNames.Length; ++code)
		{
			string? expected = Controllers.KeyNames[code] is null ? null : $"Key {Controllers.KeyNames[code]}";
			names &= Controllers.KeyButtons[code] == expected;
			if (expected is not null) names &= Controllers.KeyButton(code) == expected;
		}
		Same(names, "the key buttons are \"Key \" + the name of the key, null where the code is not a key");
		bool joysticks = true;
		for (int port = 1; port <= 2; ++port)
			for (int i = 0; i < Controllers.JoystickNames.Length; ++i)
				joysticks &= Controllers.JoystickButtons[port - 1][i] == $"P{port} Joystick {Controllers.JoystickNames[i]}" && Controllers.JoystickButtons[port - 1][i] == Controllers.Joystick(port, Controllers.JoystickNames[i]);
		Same(joysticks, "the joystick buttons are the names Controllers.Joystick makes");
		Same(Controllers.Port1MouseLeft == $"P1 {Controllers.MouseLeft}" && Controllers.Port1MouseMiddle == $"P1 {Controllers.MouseMiddle}" && Controllers.Port1MouseRight == $"P1 {Controllers.MouseRight}"
			&& Controllers.Port1MouseX == $"P1 {Controllers.MouseX}" && Controllers.Port1MouseY == $"P1 {Controllers.MouseY}", "the mouse buttons and axes are the names the controller definition makes");
		var definition = Controllers.Define(new Puae6SyncSettings { ControllerPort1 = Puae6Port1.Mouse, ControllerPort2 = Puae6Port2.Joystick }, 1);
		Same(Controllers.KeyButtons.Where(b => b is not null).All(b => definition.BoolButtons.Contains(b!)) && Controllers.JoystickButtons[1].All(b => definition.BoolButtons.Contains(b))
			&& definition.BoolButtons.Contains(Controllers.Port1MouseLeft) && definition.Axes.ContainsKey(Controllers.Port1MouseX), "every name is a button or axis of the controller definition");

		// ---- the picture ----
		var random = new Random(34);
		bool pixels = true;
		foreach (int length in new[] { 0, 1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 31, 33, 63, 65, 100, 755, 756, 757, 1512 })
			foreach (int offset in new[] { 0, 1, 3 })
			{
				var source = new int[length + offset + 5];
				for (int i = 0; i < source.Length; ++i) source[i] = random.Next(int.MinValue, int.MaxValue);
				var target = new int[length + offset + 5];
				var guard = new int[target.Length];
				for (int i = 0; i < target.Length; ++i) target[i] = guard[i] = 0x5A5A5A5A + i;
				PixelCopy.XrgbToArgb(source.AsSpan(offset, length), target.AsSpan(offset, length));
				for (int i = 0; i < target.Length; ++i)
				{
					int expected = i >= offset && i < offset + length ? source[i] | unchecked((int)0xFF000000) : guard[i];       // outside the span nothing is written
					pixels &= target[i] == expected;
				}
			}
		Same(pixels, "XrgbToArgb sets the alpha of every pixel and writes nothing else, for every length and offset");
		bool throws = false;
		try { PixelCopy.XrgbToArgb(new int[8], new int[7]); } catch (ArgumentException) { throws = true; }
		Same(throws, "a target shorter than the source is refused, not written past");
		var engineLike = new int[16];                                  // the engine's pixels have X = 0
		for (int i = 0; i < engineLike.Length; ++i) engineLike[i] = random.Next(0, 0x1000000);
		var argb = new int[16];
		PixelCopy.XrgbToArgb(engineLike, argb);
		Same(Enumerable.Range(0, 16).All(i => (uint)argb[i] == (0xFF000000u | (uint)engineLike[i])), "an XRGB8888 pixel becomes the same colour with alpha 255");

		// ---- the cost ----
		int iterations = int.Parse(options.GetValueOrDefault("iterations", "2000"));
		const int width = 756, height = 574;                          // the A500 PAL raster the core copies at every unit
		int stride = width;                                            // ints
		int* native = (int*)NativeMemory.Alloc((nuint)(stride * height * 4));
		for (int i = 0; i < stride * height; ++i) native[i] = random.Next(0, 0x1000000);
		var video = new int[width * height];
		var pictureReference = new List<long>(); var pictureNew = new List<long>();
		void ReferencePicture()
		{
			for (int row = 0; row < height; ++row)
			{
				var source = new ReadOnlySpan<int>(native + row * stride, width);
				var target = video.AsSpan(row * width, width);
				for (int i = 0; i < target.Length; ++i) target[i] = source[i] | unchecked((int)0xFF000000);     // the loop CopyOut had
			}
		}
		void NewPicture()
		{
			for (int row = 0; row < height; ++row) PixelCopy.XrgbToArgb(new ReadOnlySpan<int>(native + row * stride, width), video.AsSpan(row * width, width));
		}
		ReferencePicture();
		var expectedVideo = (int[])video.Clone();
		Array.Clear(video);
		NewPicture();
		Same(video.AsSpan().SequenceEqual(expectedVideo), "the 756 x 574 picture is the same as the reference loop made");
		// Interleaved, one after the other in turn, so a drift of the machine hits both alike.
		for (int warm = 0; warm < 50; ++warm) { ReferencePicture(); NewPicture(); }
		for (int n = 0; n < iterations; ++n)
		{
			long t0 = Stopwatch.GetTimestamp(); ReferencePicture(); long t1 = Stopwatch.GetTimestamp(); NewPicture(); long t2 = Stopwatch.GetTimestamp();
			pictureReference.Add(t1 - t0); pictureNew.Add(t2 - t1);
		}
		NativeMemory.Free(native);

		var controller = new ScriptedController(definition);
		controller.Clear();
		foreach (var key in new[] { "Key Space", "Key Return", "Key Left Shift", "Key A" }) controller.Press(key);
		controller.Press(Controllers.Joystick(2, "Button 1"));
		uint[] keysReference = new uint[4], keysNew = new uint[4];
		void ReferenceKeys(uint[] keys)
		{
			for (int code = 0; code < Controllers.KeyNames.Length; ++code)
			{
				if (Controllers.KeyNames[code] is null || code == Controllers.CapsLockCode) continue;
				if (controller.IsPressed($"Key {Controllers.KeyNames[code]}")) keys[code / 32] |= 1u << (code % 32);       // the form BuildInput had
			}
			for (int port = 1; port <= 2; ++port)
				for (int i = 0; i < Controllers.JoystickNames.Length; ++i)
					if (controller.IsPressed(Controllers.Joystick(port, Controllers.JoystickNames[i]))) keys[3] |= 1u << i;
		}
		void NewKeys(uint[] keys)
		{
			var buttons = Controllers.KeyButtons;
			for (int code = 0; code < buttons.Length; ++code)
			{
				string? button = buttons[code];
				if (button is null || code == Controllers.CapsLockCode) continue;
				if (controller.IsPressed(button)) keys[code / 32] |= 1u << (code % 32);
			}
			for (int port = 1; port <= 2; ++port)
			{
				var joystick = Controllers.JoystickButtons[port - 1];
				for (int i = 0; i < joystick.Length; ++i)
					if (controller.IsPressed(joystick[i])) keys[3] |= 1u << i;
			}
		}
		ReferenceKeys(keysReference); NewKeys(keysNew);
		Same(keysReference.AsSpan().SequenceEqual(keysNew) && keysNew.Any(k => k != 0), "the questions asked of the controller give the same packet, with something pressed");
		var keysReferenceTimes = new List<long>(); var keysNewTimes = new List<long>();
		long referenceBytes = 0, newBytes = 0;
		for (int warm = 0; warm < 200; ++warm) { ReferenceKeys(keysReference); NewKeys(keysNew); }
		for (int n = 0; n < iterations; ++n)
		{
			long a0 = GC.GetAllocatedBytesForCurrentThread(); long t0 = Stopwatch.GetTimestamp(); ReferenceKeys(keysReference); long t1 = Stopwatch.GetTimestamp(); long a1 = GC.GetAllocatedBytesForCurrentThread();
			NewKeys(keysNew); long t2 = Stopwatch.GetTimestamp(); long a2 = GC.GetAllocatedBytesForCurrentThread();
			keysReferenceTimes.Add(t1 - t0); keysNewTimes.Add(t2 - t1); referenceBytes += a1 - a0; newBytes += a2 - a1;
		}

		static double[] Ns(List<long> ticks) => ticks.Select(t => t * 1e9 / Stopwatch.Frequency).ToArray();
		var report = new Dictionary<string, object?>
		{
			["tool"] = "Ffi.dll iobench", ["iterations"] = iterations, ["checks"] = checks,
			["managed_build"] = typeof(Program).Assembly.GetCustomAttributes(typeof(DebuggableAttribute), false).Cast<DebuggableAttribute>().Any(a => a.IsJITOptimizerDisabled) ? "Debug (JIT optimizer disabled)" : "Release",
			["vector_accelerated"] = System.Numerics.Vector.IsHardwareAccelerated, ["vector_ints"] = System.Numerics.Vector<int>.Count,
			["picture_reference_ns"] = Ns(pictureReference), ["picture_new_ns"] = Ns(pictureNew),
			["keys_reference_ns"] = Ns(keysReferenceTimes), ["keys_new_ns"] = Ns(keysNewTimes),
			["keys_reference_bytes_per_call"] = referenceBytes / (double)iterations, ["keys_new_bytes_per_call"] = newBytes / (double)iterations,
		};
		File.WriteAllText(options["out"], JsonSerializer.Serialize(report) + "\n");
		Console.WriteLine($"ok iobench iterations={iterations}");
	}
}
