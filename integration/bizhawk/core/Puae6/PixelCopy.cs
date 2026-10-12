using System.Numerics;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;

namespace Puae6;

/// <summary>
/// The one transformation the core applies to the guest's picture (T34): the engine draws XRGB8888 with the X byte at zero and BizHawk wants ARGB, so every pixel gets its alpha set.
/// Vectors of the host's width where there are any (System.Numerics.Vector, which net48 and net8 both have), the scalar loop for what is left. The result is the same, pixel by pixel,
/// as <c>target[i] = source[i] | 0xFF000000</c>; the copy cannot reach past the two spans and does not keep either.
/// </summary>
public static class PixelCopy
{
	const int Opaque = unchecked((int)0xFF000000);

	// AggressiveOptimization (512; net48 does not name it and ignores it, its JIT has no tiers): the first units are not run through the unoptimized tier, where the vector operators are calls
	// and the copy takes more than twice what the scalar loop did, until the runtime gets round to promoting the method (measured: 420 us against 40 us in the first 300 calls).
	[MethodImpl((MethodImplOptions)512)]
	public static void XrgbToArgb(ReadOnlySpan<int> source, Span<int> target)
	{
		if (target.Length < source.Length) throw new ArgumentException("the target is shorter than the source", nameof(target));
		target = target.Slice(0, source.Length);
		int done = 0;
		if (Vector.IsHardwareAccelerated && source.Length >= Vector<int>.Count)
		{
			var wide = MemoryMarshal.Cast<int, Vector<int>>(source);
			var wideTarget = MemoryMarshal.Cast<int, Vector<int>>(target);
			var alpha = new Vector<int>(Opaque);
			for (int i = 0; i < wide.Length; ++i) wideTarget[i] = wide[i] | alpha;
			done = wide.Length * Vector<int>.Count;
		}
		for (int i = done; i < source.Length; ++i) target[i] = source[i] | Opaque;
	}
}
