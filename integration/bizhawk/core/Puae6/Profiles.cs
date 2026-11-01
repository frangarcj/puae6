#nullable enable

using System.Collections.Generic;
using System.ComponentModel;
using System.Reflection;
using System.Runtime.CompilerServices;

namespace Puae6;

/// <summary>
/// Complete, valid machine baselines offered by the BizHawk settings editor. This is intentionally
/// not a sync-setting field: movies, states and existing config files keep storing only concrete values.
/// </summary>
public enum Puae6MachineProfile
{
	A500,
	A600,
	A600Puae5,
	A1200,
	A1200Fast8,
	A500Original,
	A500Plus,
	A1000,
	A2000Original,
	A2000,
	A4030Normal,
	CD32,
	CD32Fast8,
	A4040Normal,
	A4060Normal,
	CDTV,
}

public static class Puae6MachineProfiles
{
	public static Puae6SyncSettings Create(Puae6MachineProfile profile)
	{
		var settings = new Puae6SyncSettings();
		switch (profile)
		{
			case Puae6MachineProfile.A500: settings.Machine = Puae6Machine.A500; break;
			case Puae6MachineProfile.A600: settings.Machine = Puae6Machine.A600; break;
			case Puae6MachineProfile.A600Puae5:
				settings.Machine = Puae6Machine.A600;
				settings.ChipMemory = Puae6Chip.MB2;
				settings.FastMemory = Puae6Fast.MB8;
				break;
			case Puae6MachineProfile.A1200: settings.Machine = Puae6Machine.A1200; break;
			case Puae6MachineProfile.A1200Fast8:
				settings.Machine = Puae6Machine.A1200;
				settings.FastMemory = Puae6Fast.MB8;
				break;
			case Puae6MachineProfile.A500Original: settings.Machine = Puae6Machine.A500OG; break;
			case Puae6MachineProfile.A500Plus: settings.Machine = Puae6Machine.A500PLUS; break;
			case Puae6MachineProfile.A1000: settings.Machine = Puae6Machine.A1000; break;
			case Puae6MachineProfile.A2000Original: settings.Machine = Puae6Machine.A2000OG; break;
			case Puae6MachineProfile.A2000: settings.Machine = Puae6Machine.A2000; break;
			case Puae6MachineProfile.A4030Normal:
				settings.Machine = Puae6Machine.A4030;
				settings.CpuCompatibility = Puae6CpuCompatibility.Normal;
				break;
			case Puae6MachineProfile.CD32: settings.Machine = Puae6Machine.CD32; break;
			case Puae6MachineProfile.CD32Fast8:
				settings.Machine = Puae6Machine.CD32;
				settings.FastMemory = Puae6Fast.MB8;
				break;
			case Puae6MachineProfile.A4040Normal:
				settings.Machine = Puae6Machine.A4040;
				settings.CpuModel = Puae6Cpu.M68040;
				settings.CpuCompatibility = Puae6CpuCompatibility.Normal;
				settings.FpuModel = Puae6Fpu.M68040;
				break;
			case Puae6MachineProfile.A4060Normal:
				settings.Machine = Puae6Machine.A4040;
				settings.CpuModel = Puae6Cpu.M68060;
				settings.CpuCompatibility = Puae6CpuCompatibility.Normal;
				settings.FpuModel = Puae6Fpu.M68060;
				break;
			case Puae6MachineProfile.CDTV: settings.Machine = Puae6Machine.CDTV; break;
			default: throw new ArgumentOutOfRangeException(nameof(profile));
		}
		return settings;
	}

	public static void Apply(Puae6SyncSettings settings, Puae6MachineProfile profile)
	{
		if (settings is null) throw new ArgumentNullException(nameof(settings));
		var baseline = Create(profile);
		foreach (var property in typeof(Puae6SyncSettings).GetProperties(BindingFlags.Instance | BindingFlags.Public))
			if (property.CanRead && property.CanWrite) property.SetValue(settings, property.GetValue(baseline));
	}

	public static Puae6MachineProfile Infer(Puae6SyncSettings settings) => settings.Machine switch
	{
		Puae6Machine.A500 => Puae6MachineProfile.A500,
		Puae6Machine.A600 when settings.ChipMemory == Puae6Chip.MB2 || settings.FastMemory == Puae6Fast.MB8 => Puae6MachineProfile.A600Puae5,
		Puae6Machine.A600 => Puae6MachineProfile.A600,
		Puae6Machine.A1200 when settings.FastMemory == Puae6Fast.MB8 => Puae6MachineProfile.A1200Fast8,
		Puae6Machine.A1200 => Puae6MachineProfile.A1200,
		Puae6Machine.A500OG => Puae6MachineProfile.A500Original,
		Puae6Machine.A500PLUS => Puae6MachineProfile.A500Plus,
		Puae6Machine.A1000 => Puae6MachineProfile.A1000,
		Puae6Machine.A2000OG => Puae6MachineProfile.A2000Original,
		Puae6Machine.A2000 => Puae6MachineProfile.A2000,
		Puae6Machine.A4030 => Puae6MachineProfile.A4030Normal,
		Puae6Machine.CD32 when settings.FastMemory == Puae6Fast.MB8 => Puae6MachineProfile.CD32Fast8,
		Puae6Machine.CD32 => Puae6MachineProfile.CD32,
		Puae6Machine.A4040 when settings.CpuModel == Puae6Cpu.M68060 || settings.FpuModel == Puae6Fpu.M68060 => Puae6MachineProfile.A4060Normal,
		Puae6Machine.A4040 => Puae6MachineProfile.A4040Normal,
		Puae6Machine.CDTV => Puae6MachineProfile.CDTV,
		_ => throw new ArgumentOutOfRangeException(nameof(settings), settings.Machine, "Unknown PUAE6 machine"),
	};
}

/// <summary>PropertyGrid metadata for the virtual profile and profile-relative bold overrides.</summary>
public sealed partial class Puae6SyncSettings : ICustomTypeDescriptor
{
	private sealed class ProfileSelection(Puae6MachineProfile profile) { public Puae6MachineProfile Profile = profile; }
	private static readonly ConditionalWeakTable<Puae6SyncSettings, ProfileSelection> ProfileSelections = new();

	private Puae6MachineProfile ProfileOf()
		=> ProfileSelections.GetValue(this, value => new(Puae6MachineProfiles.Infer(value))).Profile;

	private void SelectProfile(Puae6MachineProfile profile)
	{
		Puae6MachineProfiles.Apply(this, profile);
		ProfileSelections.Remove(this);
		ProfileSelections.Add(this, new(profile));
	}

	AttributeCollection ICustomTypeDescriptor.GetAttributes() => TypeDescriptor.GetAttributes(typeof(Puae6SyncSettings));
	string? ICustomTypeDescriptor.GetClassName() => TypeDescriptor.GetClassName(typeof(Puae6SyncSettings));
	string? ICustomTypeDescriptor.GetComponentName() => TypeDescriptor.GetComponentName(typeof(Puae6SyncSettings));
	TypeConverter ICustomTypeDescriptor.GetConverter() => TypeDescriptor.GetConverter(typeof(Puae6SyncSettings));
	EventDescriptor? ICustomTypeDescriptor.GetDefaultEvent() => TypeDescriptor.GetDefaultEvent(typeof(Puae6SyncSettings));
	PropertyDescriptor? ICustomTypeDescriptor.GetDefaultProperty() => TypeDescriptor.GetDefaultProperty(typeof(Puae6SyncSettings));
	object? ICustomTypeDescriptor.GetEditor(Type editorBaseType) => TypeDescriptor.GetEditor(typeof(Puae6SyncSettings), editorBaseType);
	EventDescriptorCollection ICustomTypeDescriptor.GetEvents() => TypeDescriptor.GetEvents(typeof(Puae6SyncSettings));
	EventDescriptorCollection ICustomTypeDescriptor.GetEvents(Attribute[]? attributes) => TypeDescriptor.GetEvents(typeof(Puae6SyncSettings), attributes ?? Array.Empty<Attribute>());
	PropertyDescriptorCollection ICustomTypeDescriptor.GetProperties() => Properties(null);
	PropertyDescriptorCollection ICustomTypeDescriptor.GetProperties(Attribute[]? attributes) => Properties(attributes);
	object ICustomTypeDescriptor.GetPropertyOwner(PropertyDescriptor? property) => this;

	private PropertyDescriptorCollection Properties(Attribute[]? attributes)
	{
		var source = TypeDescriptor.GetProperties(typeof(Puae6SyncSettings), attributes);
		var result = new List<PropertyDescriptor>(source.Count);
		result.Add(new ProfileProperty());
		foreach (PropertyDescriptor property in source)
			if (property.Name != nameof(Machine)) result.Add(new ProfileValueProperty(property));
		return new(result.ToArray(), true);
	}

	private sealed class ProfileProperty() : PropertyDescriptor(
		"MachineProfile",
		[new DisplayNameAttribute("Machine profile"),
		 new DescriptionAttribute("Applies a complete valid machine baseline. Values changed afterwards are shown in bold."),
		 new RefreshPropertiesAttribute(RefreshProperties.All)])
	{
		public override Type ComponentType => typeof(Puae6SyncSettings);
		public override bool IsReadOnly => false;
		public override Type PropertyType => typeof(Puae6MachineProfile);
		public override bool CanResetValue(object component) => false;
		public override object GetValue(object? component) => ((Puae6SyncSettings)component!).ProfileOf();
		public override void ResetValue(object component) { }
		public override void SetValue(object? component, object? value)
		{
			if (component is not Puae6SyncSettings settings || value is not Puae6MachineProfile profile) throw new ArgumentException("Invalid PUAE6 machine profile", nameof(value));
			settings.SelectProfile(profile);
			OnValueChanged(component, EventArgs.Empty);
		}
		public override bool ShouldSerializeValue(object component) => false;
	}

	private sealed class ProfileValueProperty(PropertyDescriptor property) : PropertyDescriptor(property)
	{
		private static Puae6SyncSettings Baseline(object component) => Puae6MachineProfiles.Create(((Puae6SyncSettings)component).ProfileOf());
		public override Type ComponentType => property.ComponentType;
		public override bool IsReadOnly => property.IsReadOnly;
		public override Type PropertyType => property.PropertyType;
		public override bool CanResetValue(object component) => !IsReadOnly && ShouldSerializeValue(component);
		public override object? GetValue(object? component) => property.GetValue(component);
		public override void ResetValue(object component)
		{
			property.SetValue(component, property.GetValue(Baseline(component)));
			OnValueChanged(component, EventArgs.Empty);
		}
		public override void SetValue(object? component, object? value) => property.SetValue(component, value);
		public override bool ShouldSerializeValue(object component)
			=> !Equals(property.GetValue(component), property.GetValue(Baseline(component)));
		public override bool SupportsChangeEvents => property.SupportsChangeEvents;
	}
}
