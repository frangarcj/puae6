using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;

using BizHawk.Common;
using BizHawk.Emulation.Common;

namespace BizHawk.Emulation.Cores.Computers.Amiga
{
	public partial class UAE : ISettable<UAE.UAESettings, UAE.UAESyncSettings>
	{
		public enum MachineConfig
		{
			[Display(Name = "A500 OCS KS1.3 512K 512K")]
			A500_OCS_130_512K_512K,
			[Display(Name = "A600 ECS KS2.05 2M")]
			A600_ECS_205_2M,
			[Display(Name = "A1200 AGA KS3.1 2M 8M")]
			A1200_AGA_310_2M_8M,
			[Display(Name = "A4000 AGA KS3.1 2M 8M")]
			A4000_AGA_310_2M_8M,
			[Display(Name = "CD32 AGA KS3.1")]
			CD32,
			[Display(Name = "CDTV ECS KS1.3 1M")]
			CDTV,
		}

		public enum CdController
		{
			Auto,
			CD32,
			IDE,
			None
		}

		public enum CpuModel
		{
			[Display(Name = "68000")]
			_68000 = 68000,
			[Display(Name = "68010")]
			_68010 = 68010,
			[Display(Name = "68020")]
			_68020 = 68020,
			[Display(Name = "68030")]
			_68030 = 68030,
			[Display(Name = "68040")]
			_68040 = 68040,
			[Display(Name = "68060")]
			_68060 = 68060,
			Auto
		}

		public enum ChipsetCompatible
		{
			A500,
			A600,
			A1200,
			A4000,
			CD32,
			CDTV,
			Auto
		}

		public enum Chipset
		{
			OCS,
			[Display(Name = "ECS Agnus")]
			ECS_Agnus,
			[Display(Name = "ECS Denise")]
			ECS_Denise,
			ECS,
			AGA,
			Auto
		}

		public enum VideoStandard
		{
			PAL,
			NTSC
		}

		public enum ChipMemory
		{
			[Display(Name = "512KB")]
			KB_512 = 1,
			[Display(Name = "1MB")]
			MB_1,
			[Display(Name = "1.5MB")]
			MB_1_5,
			[Display(Name = "2MB")]
			MB_2,
			[Display(Name = "2.5MB")]
			MB_2_5,
			[Display(Name = "3MB")]
			MB_3,
			[Display(Name = "3.5MB")]
			MB_3_5,
			[Display(Name = "4MB")]
			MB_4,
			[Display(Name = "4.5MB")]
			MB_4_5,
			[Display(Name = "5MB")]
			MB_5,
			[Display(Name = "5.5MB")]
			MB_5_5,
			[Display(Name = "6MB")]
			MB_6,
			[Display(Name = "6.5MB")]
			MB_6_5,
			[Display(Name = "7MB")]
			MB_7,
			[Display(Name = "7.5MB")]
			MB_7_5,
			[Display(Name = "8MB")]
			MB_8,
			Auto
		}

		public enum SlowMemory
		{
			[Display(Name = "0")]
			KB_0 = 0,
			[Display(Name = "512KB")]
			KB_512 = 2,
			[Display(Name = "1MB")]
			MB_1 = 4,
			[Display(Name = "1.5MB")]
			MB_1_5 = 6,
			Auto
		}

		public enum MotherboardFastRam
		{
			None = 0,
			[Display(Name = "1MB")]
			MB_1 = 1,
			[Display(Name = "2MB")]
			MB_2 = 2,
			[Display(Name = "4MB")]
			MB_4 = 4,
			[Display(Name = "8MB")]
			MB_8 = 8,
			[Display(Name = "16MB")]
			MB_16 = 16,
			[Display(Name = "32MB")]
			MB_32 = 32,
			[Display(Name = "64MB")]
			MB_64 = 64,
		}

		public enum DriveType
		{
			DRV_NONE = -1,
			DRV_35_DD = 0,
			DRV_35_HD,
			DRV_525_SD,
			DRV_35_DD_ESCOM,
			DRV_PC_525_ONLY_40,
			DRV_PC_35_ONLY_80,
			DRV_PC_525_40_80,
			DRV_525_DD,
			DRV_FB
		}

		public enum FloppySpeed
		{
			[Display(Name = "100%")]
			_100 = 100,
			[Display(Name = "200%")]
			_200 = 200,
			[Display(Name = "400%")]
			_400 = 400,
			[Display(Name = "800%")]
			_800 = 800,
			Turbo = 0
		}

		private void CreateArguments(UAESyncSettings settings)
		{
			_args =
			[
				"uae",
			];

			switch(EffectiveMachineConfig(settings))
			{
				case MachineConfig.A500_OCS_130_512K_512K:
					_chipsetCompatible = Enum.GetName(typeof(ChipsetCompatible), ChipsetCompatible.A500);
					AppendSetting(
					[
						"cpu_model=" + (int)CpuModel._68000,
						"chipset=" + Chipset.OCS,
						"chipset_compatible=" + _chipsetCompatible,
						"chipmem_size=" + (int)ChipMemory.KB_512,
						"bogomem_size=" + (int)SlowMemory.KB_512,
						"fastmem_size=0",
					]);
					EnableCycleExact();
					break;
				case MachineConfig.A600_ECS_205_2M:
					_chipsetCompatible = Enum.GetName(typeof(ChipsetCompatible), ChipsetCompatible.A600);
					AppendSetting(
					[
						"cpu_model=" + (int)CpuModel._68000,
						"chipset=" + Chipset.ECS,
						"chipset_compatible=" + _chipsetCompatible,
						"chipmem_size=" + (int)ChipMemory.MB_2,
						"bogomem_size=" + (int)SlowMemory.KB_0,
						"fastmem_size=0",
					]);
					EnableCycleExact();
					break;
				case MachineConfig.A1200_AGA_310_2M_8M:
					_chipsetCompatible = Enum.GetName(typeof(ChipsetCompatible), ChipsetCompatible.A1200);
					AppendSetting(
					[
						"cpu_model=" + (int)CpuModel._68020,
						"chipset=" + Chipset.AGA,
						"chipset_compatible=" + _chipsetCompatible,
						"chipmem_size=" + (int)ChipMemory.MB_2,
						"bogomem_size=" + (int)SlowMemory.KB_0,
						"fastmem_size=0",
						"cpu_multiplier=4",
					]);
					EnableCycleExact();
					break;
				case MachineConfig.A4000_AGA_310_2M_8M:
					_chipsetCompatible = Enum.GetName(typeof(ChipsetCompatible), ChipsetCompatible.A4000);
					AppendSetting(
					[
						"cpu_model=" + (int)CpuModel._68040,
						"fpu_model=68040",
						"mmu_model=68040",
						"chipset=" + Chipset.AGA,
						"chipset_compatible=" + _chipsetCompatible,
						"chipmem_size=" + (int)ChipMemory.MB_2,
						"bogomem_size=" + (int)SlowMemory.KB_0,
						"fastmem_size=8",
						"a3000mem_size=" + (int)settings.MBFastRam,
					]);
					break;
				case MachineConfig.CD32:
					_chipsetCompatible = Enum.GetName(typeof(ChipsetCompatible), ChipsetCompatible.CD32);
					AppendSetting("quickstart=CD32,0");
					break;
				case MachineConfig.CDTV:
					// KS1.3-class machine (68000, ECS Agnus, 1MB chip) with a built-in CD drive and no floppy
					// (quickstart config 0); the Kickstart itself is the plain A500 KS1.3 ROM, see _kickstartFirmwareId.
					_chipsetCompatible = Enum.GetName(typeof(ChipsetCompatible), ChipsetCompatible.CDTV);
					AppendSetting("quickstart=CDTV,0");
					break;
			}

			_kickstartFirmwareId = _chipsetCompatible == "CDTV" ? "A500" : _chipsetCompatible;

			if (settings.CpuModel != CpuModel.Auto)
			{
				AppendSetting("cpu_model=" + (int)settings.CpuModel);

				if (settings.CpuModel < CpuModel._68030)
				{
					EnableCycleExact();
				}
			}

			if (settings.Chipset != Chipset.Auto)
			{
				AppendSetting("chipset=" + (int)settings.Chipset);
			}

			if (settings.ChipsetCompatible != ChipsetCompatible.Auto)
			{
				AppendSetting("chipset_compatible="
					+ Enum.GetName(typeof(ChipsetCompatible), settings.ChipsetCompatible));
			}

			if (settings.ChipMemory != ChipMemory.Auto)
			{
				AppendSetting("chipmem_size=" + (int)settings.ChipMemory);
			}

			if (settings.SlowMemory != SlowMemory.Auto)
			{
				AppendSetting("bogomem_size=" + (int)settings.SlowMemory);
			}

			if (settings.FastMemory != LibUAE.FASTMEM_AUTO)
			{
				AppendSetting("fastmem_size=" + settings.FastMemory);
			}

			if (settings.Region == VideoStandard.NTSC)
			{
				AppendSetting("ntsc=true");
			}

			AppendSetting("input.mouse_speed=" + settings.MouseSpeed);
			AppendSetting("sound_stereo_separation=" + settings.StereoSeparation / 10);

			if (!DeterministicEmulation)
			{
				AppendSetting("floppy_speed=" + (int)settings.FloppySpeed);
			}

			for (int port = 0; port <= 1; port++)
			{
				var type = port == 0
					? settings.ControllerPort1
					: settings.ControllerPort2;
				AppendSetting(type is LibUAE.ControllerType.None
					? $"joyport{port}=none"
					: $"joyport{port}mode={type.ToString().ToLowerInvariant()}");
			}
		}

		private MachineConfig EffectiveMachineConfig(UAESyncSettings settings)
		{
			if (_isCd32)
			{
				return MachineConfig.CD32;
			}

			if (_isCdtv)
			{
				return MachineConfig.CDTV;
			}

			// WHDLoad itself needs Kickstart 2.0 or later; the KS1.3-class A500 and CDTV configs cannot run it
			if (WhdLoadForcesA1200(settings.MachineConfig))
			{
				Console.WriteLine(
					$"WHDLoad archive loaded with a {(settings.MachineConfig == MachineConfig.CDTV ? "CDTV" : "A500 KS1.3")} "
					+ "configuration, using the A1200 configuration instead");
				return MachineConfig.A1200_AGA_310_2M_8M;
			}

			return settings.MachineConfig;
		}

		private void EnableCycleExact()
		{
			AppendSetting(
			[
				"cpu_compatible=true",
				"cpu_cycle_exact=true",
				"cpu_memory_cycle_exact=true",
				"blitter_cycle_exact=true",
			]);
		}

		private void AppendSetting(List<string> settings)
		{
			foreach (var s in settings)
			{
				AppendSetting(s);
			}
		}

		private void AppendSetting(string setting)
		{
			_args.AddRange(
			[
				"-s", setting
			]);
		}

		private UAESettings _settings = new();
		public UAESettings GetSettings()
			=> _settings.Clone();

		public PutSettingsDirtyBits PutSettings(UAESettings o)
		{
			_settings = o.Clone();
			_uae.SetCDVolume(_settings.CdVolume);
			return PutSettingsDirtyBits.None;
		}

		private UAESyncSettings _syncSettings;
		public UAESyncSettings GetSyncSettings()
			=> _syncSettings.Clone();

		public PutSettingsDirtyBits PutSyncSettings(UAESyncSettings o)
		{
			var ret = UAESyncSettings.NeedsReboot(_syncSettings, o);
			_syncSettings = o;
			return ret ? PutSettingsDirtyBits.RebootCore : PutSettingsDirtyBits.None;
		}

		[CoreSettings]
		public class UAESettings
		{
			[DisplayName("CD audio volume")]
			[Description("CD-DA volume in percent (0 - 100). Applies to CD32/CDTV audio tracks only.")]
			[Range(0, 100)]
			[DefaultValue(100)]
			[TypeConverter(typeof(ConstrainedIntConverter))]
			public int CdVolume { get; set; }

			public UAESettings()
				=> SettingsUtil.SetDefaultValues(this);

			public UAESettings Clone()
				=> (UAESettings)MemberwiseClone();
		}

		[CoreSettings]
		public class UAESyncSettings
		{
			[DisplayName("Machine configuration")]
			[Description("'CDTV' cannot be auto-detected from a loaded CD (unlike CD32) - pick it explicitly for "
				+ "CDTV titles. It always keeps its own built-in CD drive (the CD controller setting below is "
				+ "ignored) and boots into the CDTV welcome screen with no disc loaded.")]
			[DefaultValue(MachineConfig.A500_OCS_130_512K_512K)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public MachineConfig MachineConfig { get; set; }

			[DisplayName("CD controller")]
			[Description("How a loaded CD is attached when a hard disk (.hdf) or WHDLoad archive is also loaded. "
				+ "'Auto': a CD on its own still boots into CD32 as before; a CD together with a hard disk or "
				+ "WHDLoad archive attaches the CD to the IDE port instead, keeping whichever of A600/A1200/A4000 "
				+ "machine configuration was picked - except on the default A500, which has no IDE port, so it "
				+ "stays on CD32. 'CD32' and 'IDE' force one or the other; IDE requires an A600, A1200 or A4000 "
				+ "machine configuration. 'None' does not attach a loaded CD at all. Note that on the Amiga side, "
				+ "an IDE CD drive still needs a CDFS filesystem installed on the hard disk to be visible - the "
				+ "core only exposes the drive. Ignored when the machine configuration is CDTV, which always uses "
				+ "its own built-in CD controller.")]
			[DefaultValue(CdController.Auto)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public CdController CdController { get; set; }

			[DisplayName("CPU model")]
			[Description("Overrides machine configuration.")]
			[DefaultValue(CpuModel.Auto)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public CpuModel CpuModel { get; set; }

			[DisplayName("Chipset compatible")]
			[Description("Overrides machine configuration.")]
			[DefaultValue(ChipsetCompatible.Auto)]
			public ChipsetCompatible ChipsetCompatible { get; set; }

			[DisplayName("Chipset")]
			[Description("Overrides machine configuration.")]
			[DefaultValue(Chipset.Auto)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public Chipset Chipset { get; set; }

			[DisplayName("Chip memory")]
			[Description("Size of chip-memory.  Overrides machine configuration.")]
			[DefaultValue(ChipMemory.Auto)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public ChipMemory ChipMemory { get; set; }

			[DisplayName("Slow memory")]
			[Description("Size of bogo-memory at 0xC00000.  Overrides machine configuration.")]
			[DefaultValue(SlowMemory.Auto)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public SlowMemory SlowMemory { get; set; }

			[DisplayName("Fast memory")]
			[Description("Size in megabytes of fast-memory.  -1 means Auto.  Overrides machine configuration.")]
			[Range(LibUAE.FASTMEM_AUTO, 512)]
			[DefaultValue(LibUAE.FASTMEM_AUTO)]
			[TypeConverter(typeof(ConstrainedIntConverter))]
			public int FastMemory { get; set; }

			[DisplayName("Motherboard Fast RAM")]
			[Description("Only applicable for A4000.")]
			[DefaultValue(MotherboardFastRam.None)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public MotherboardFastRam MBFastRam { get; set; }

			[DisplayName("Controller port 1")]
			[Description("")]
			[DefaultValue(LibUAE.ControllerType.Mouse)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public LibUAE.ControllerType ControllerPort1 { get; set; }

			[DisplayName("Controller port 2")]
			[Description("")]
			[DefaultValue(LibUAE.ControllerType.DJoy)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public LibUAE.ControllerType ControllerPort2 { get; set; }

			[DisplayName("Mouse speed")]
			[Description("Mouse speed in percents (1% - 1000%).  Adjust if there's mismatch between emulated and host mouse movement.  Note that maximum mouse movement is still 127 pixels due to Amiga hardware limitations.")]
			[Range(1, 1000)]
			[DefaultValue(100)]
			[TypeConverter(typeof(ConstrainedIntConverter))]
			public int MouseSpeed { get; set; }

			[DisplayName("Stereo separation")]
			[Description("Stereo separation in percents.  100% is full separation, 0% is mono mode.")]
			[Range(0, 100)]
			[DefaultValue(70)]
			[TypeConverter(typeof(ConstrainedIntConverter))]
			public int StereoSeparation { get; set; }

			[DisplayName("Floppy disk drives")]
			[Description("How many floppy disk drives to emulate (0 - 4).")]
			[Range(0, 4)]
			[DefaultValue(1)]
			[TypeConverter(typeof(ConstrainedIntConverter))]
			public int FloppyDrives { get; set; }

			[DisplayName("Video standard")]
			[Description("Determines resolution and framerate.")]
			[DefaultValue(VideoStandard.PAL)]
			public VideoStandard Region { get; set; }

			[DisplayName("Floppy drive speed")]
			[Description("Default speed is 300RPM.  'Turbo' removes disk rotation emulation.  This is a speedhack, not available for movies.")]
			[DefaultValue(FloppySpeed._100)]
			[TypeConverter(typeof(DescribableEnumConverter))]
			public FloppySpeed FloppySpeed { get; set; }

			[DisplayName("CD32 FMV Cartridge")]
			[Description("Plugs the Full Motion Video module into the CD32's expansion slot. Needs the CD32FMV "
				+ "firmware (the FMV cartridge ROM) and is required by a handful of CD32 games (Cannon Fodder, "
				+ "Defender of the Crown II, Microcosm, ...) to play their MPEG "
				+ "movies, and by White Book Video CDs (the module's built-in CD32 Movies player). Only meaningful "
				+ "when the effective machine configuration is CD32; ignored otherwise.")]
			[DefaultValue(false)]
			public bool Cd32Fmv { get; set; }

			public UAESyncSettings()
				=> SettingsUtil.SetDefaultValues(this);

			public UAESyncSettings Clone()
				=> (UAESyncSettings)MemberwiseClone();

			public static bool NeedsReboot(UAESyncSettings x, UAESyncSettings y)
				=> !DeepEquality.DeepEquals(x, y);
		}
	}
}
