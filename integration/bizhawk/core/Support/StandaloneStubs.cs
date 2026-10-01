// Types of BizHawk that the pinned sources mention and the standalone build of the core does not need to be real (the Waterbox and
// emulation-service sources compile against them; EmuHawk has the real ones). MacHawk's own harnesses do the same
// (uae-cd32-headless/UaeCompileSupport.cs). Nothing here is part of the core and none of it is compiled into BizHawk.
using BizHawk.Common;

namespace BizHawk.Emulation.Common
{
	/// <summary>The part of BizHawk's database entry that ICoreFileProvider hands back with a firmware file.</summary>
	public sealed class GameInfo
	{
		public string Name { get; set; } = "";
		public string Hash { get; set; } = "";
	}

	/// <summary>The one extension of BizHawk's Extensions.cs that ControllerDefinition.cs needs, as written there (Emulation.Common/Extensions.cs
	/// depends on a dozen service interfaces that this build has no use for).</summary>
	public static class StandaloneExtensions
	{
		public static ControllerDefinition AddAxis(this ControllerDefinition def, string name, Range<int> range, int neutral, bool isReversed = false, AxisConstraint constraint = null)
		{
			def.Axes.Add(name, new AxisSpec(range, neutral, isReversed, constraint));
			return def;
		}
	}
}
