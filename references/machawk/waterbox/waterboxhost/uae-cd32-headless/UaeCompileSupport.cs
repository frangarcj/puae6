using System.Collections.ObjectModel;

namespace BizHawk.Emulation.Common
{
	[AttributeUsage(AttributeTargets.Constructor, AllowMultiple = true)]
	public sealed class CoreConstructorAttribute : Attribute
	{
		public CoreConstructorAttribute(string system) { }
	}

	public interface IGameInfo
	{
		string Name { get; }
		string System { get; }
	}

	public class GameInfo : IGameInfo
	{
		public string ForcedCore { get; set; } = string.Empty;
		public string Hash { get; set; } = string.Empty;
		public string Name { get; set; } = string.Empty;
		public bool NotInDatabase { get; set; } = true;
		public RomStatus Status { get; set; } = RomStatus.NotInDatabase;
		public string System { get; set; } = string.Empty;
		public bool this[string option] => false;
		public string? OptionValue(string option) => null;
	}

	public sealed class MemoryDomainList : ReadOnlyCollection<MemoryDomain>, IMemoryDomains
	{
		public MemoryDomainList(IList<MemoryDomain> domains) : base(domains) { }

		public MemoryDomain? this[string name] => this.FirstOrDefault(domain => domain.Name == name);
		public MemoryDomain MainMemory { get; set; } = null!;
		public bool HasSystemBus => this.Any(domain => domain.Name == "System Bus");
		public MemoryDomain SystemBus => this["System Bus"] ?? MainMemory;
		public bool Has(string name) => this[name] is not null;
	}

	public static class EmulatorExtensions
	{
		public static CoreAttribute Attributes(this IEmulator core)
			=> (CoreAttribute)Attribute.GetCustomAttribute(core.GetType(), typeof(CoreAttribute))!;

		public static ControllerDefinition AddAxis(
			this ControllerDefinition definition,
			string name,
			BizHawk.Common.Range<int> range,
			int neutral,
			bool isReversed = false,
			AxisConstraint? constraint = null)
		{
			definition.Axes.Add(name, new AxisSpec(range, neutral, isReversed, constraint));
			return definition;
		}
	}
}
