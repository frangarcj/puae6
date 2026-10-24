using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

using BizHawk.Common.StringExtensions;
using BizHawk.Emulation.Common;
using BizHawk.Emulation.Cores.Waterbox;
using BizHawk.Emulation.DiscSystem;

namespace BizHawk.Emulation.Cores.Computers.Amiga
{
	[PortedCore(
		name: CoreNames.UAE,
		author: "UAE Team",
		portedVersion: "5.0.0",
		portedUrl: "https://github.com/libretro/libretro-uae",
		isReleased: true)]
	public partial class UAE : WaterboxCore
	{
		private static readonly Configuration ConfigPAL = new()
		{
			SystemId = VSystemID.Raw.Amiga,
			MaxSamples = 8 * 1024,
			DefaultWidth = LibUAE.PAL_WIDTH,
			DefaultHeight = LibUAE.PAL_HEIGHT,
			MaxWidth = LibUAE.PAL_WIDTH,
			MaxHeight = LibUAE.PAL_HEIGHT,
			DefaultFpsNumerator = LibUAE.VIDEO_NUMERATOR_PAL,
			DefaultFpsDenominator = LibUAE.VIDEO_DENOMINATOR_PAL
		};

		private static readonly Configuration ConfigNTSC = new()
		{
			SystemId = VSystemID.Raw.Amiga,
			MaxSamples = 8 * 1024,
			DefaultWidth = LibUAE.NTSC_WIDTH,
			DefaultHeight = LibUAE.NTSC_HEIGHT,
			// games never switch region, and video dumping won't be happy, but amiga can still do it
			MaxWidth = LibUAE.PAL_WIDTH,
			MaxHeight = LibUAE.PAL_HEIGHT,
			DefaultFpsNumerator = LibUAE.VIDEO_NUMERATOR_NTSC,
			DefaultFpsDenominator = LibUAE.VIDEO_DENOMINATOR_NTSC
		};

		private readonly LibWaterboxCore.EmptyCallback _ledCallback;
		private readonly List<IRomAsset> _roms;
		private readonly List<IRomAsset> _hardDisks;
		private readonly List<IRomAsset> _whdLoadArchives;
		private readonly List<(string Name, byte[] Data)> _mountedHardDisks = new();
		private readonly List<IDiscAsset> _discAssets;
		private readonly DiscSectorReader[] _cdReaders;
		private readonly object[] _cdReaderLocks;
		private readonly LibUAE.CDData[] _cdTocs;
		private readonly LibUAE.CDTOCCallback _cdTocCallback;
		private readonly LibUAE.CDSectorCallback _cdSectorCallback;
		private readonly LibUAE _uae;
		private const int _driveNullOrEmpty = -1;
		private int[] _driveSlots;
		private List<string> _args;
		private int _currentDrive;
		private int _currentSlot;
		private bool _ejectPressed;
		private bool _insertPressed;
		private bool _nextSlotPressed;
		private bool _nextDrivePressed;
		private readonly bool _isCd32;
		private readonly bool _isCdtv;
		private readonly bool _cdOnIde;

		/// <summary>false when there is nothing the floppy swap buttons could act on, e.g. a hard disk or WHDLoad pack.</summary>
		private readonly bool _hasFloppies;
		private int _currentDisc;
		private int _correctedWidth;
		private string _chipsetCompatible = "";

		/// <summary>Firmware id for the Kickstart lookup; equal to <see cref="_chipsetCompatible"/> except for
		/// CDTV, which shares the A500's KS1.3 Kickstart but reports "CDTV" for chipset_compatible.</summary>
		private string _kickstartFirmwareId = "";
		private string GetFullName(IRomAsset rom) => Path.GetFileName(rom.RomPath.SubstringAfter('|'));
		private string GetFullName(IDiscAsset disc) => Path.GetFileName(disc.DiscData.Name.SubstringAfter('|'));

		public override int VirtualWidth => _correctedWidth;

		private void LEDCallback()
		{
			DriveLightOn = true;
		}

		[CoreConstructor(VSystemID.Raw.Amiga)]
		public UAE(CoreLoadParameters<UAESettings, UAESyncSettings> lp)
			: base(lp.Comm, lp.SyncSettings?.Region is VideoStandard.NTSC ? ConfigNTSC : ConfigPAL)
		{
			_settings = lp.Settings ?? new();
			_roms = lp.Roms.Where(r => !IsHardDisk(r) && !IsWhdLoadArchive(r)).ToList();
			_hardDisks = lp.Roms.Where(IsHardDisk).ToList();
			_whdLoadArchives = lp.Roms.Where(IsWhdLoadArchive).ToList();
			if (_hardDisks.Count + (_whdLoadArchives.Count > 0 ? WhdLoadHardDiskCount : 0) > LibUAE.MAX_HARDDISKS)
			{
				throw new InvalidOperationException($"At most {LibUAE.MAX_HARDDISKS} hard disk images are supported");
			}
			_discAssets = lp.Discs;
			_syncSettings = lp.SyncSettings ?? new();
			(_isCd32, _isCdtv, _cdOnIde) = DetermineCdAttachment();
			_syncSettings.FloppyDrives = Math.Min(LibUAE.MAX_FLOPPIES, _syncSettings.FloppyDrives);
			_hasFloppies = !_isCd32 && !_isCdtv && _roms.Count > 0 && _syncSettings.FloppyDrives > 0;
			DeterministicEmulation = lp.DeterministicEmulationRequested || _syncSettings.FloppySpeed is FloppySpeed._100;
			var filesToRemove = new List<string>();
			_cdReaders = _discAssets.Select(d => new DiscSectorReader(d.DiscData)
			{
				// The CD32 FMV cartridge's cdmpeg.device reads its MPEG movie track as
				// plain 2048-byte blocks even though CD32 movie tracks are Mode 2 Form 2
				// sectors (2324 bytes of payload, no ECC/EDC). Real UAE's raw-image CD
				// backend (blkdev_cdimage.c's do_read()) never inspects the sector for a
				// 2048-byte request -- it just copies the requested byte range verbatim --
				// so match that instead of the default policy of throwing on Form 2.
				Policy = { UserData2048Mode = DiscSectorReaderPolicy.EUserData2048Mode.InspectSector_AssumeForm1 },
			}).ToArray();
			_cdReaderLocks = _cdReaders.Select(_ => new object()).ToArray();
			_cdTocs = _discAssets.Select(d => CreateCDData(d.DiscData)).ToArray();
			_cdTocCallback = CDGetTOC;
			_cdSectorCallback = CDRead;
			_currentDisc = _discAssets.Count > 0 ? 0 : -1;

			Ports = [
				_syncSettings.ControllerPort1,
				_syncSettings.ControllerPort2
			];
			_driveSlots = Enumerable.Repeat(_driveNullOrEmpty, LibUAE.MAX_FLOPPIES).ToArray();
			// a disc can produce read activity regardless of which controller it ends up on (CD32 or IDE)
			DriveLightEnabled = _syncSettings.FloppyDrives > 0 || _discAssets.Count > 0;

			UpdateVideoStandard(true);
			CreateArguments(_syncSettings);
			ControllerDefinition = CreateControllerDefinition(_syncSettings);
			_ledCallback = LEDCallback;

			_uae = PreInit<LibUAE>(new WaterboxOptions
			{
				Filename = "uae.wbx",
				// the archive filesystem used for WHDLoad packs allocates its directory and unpack buffers on the
				// sbrk heap, and so does the SPS decoder (a copy of the IPF plus every decoded track)
				SbrkHeapSizeKB = 16 * 1024,
				SealedHeapSizeKB = 512,
				InvisibleHeapSizeKB = 512,
				PlainHeapSizeKB = 512,
				MmapHeapSizeKB = 20 * 1024 * 8 + (uint)_hardDisks.Sum(HardDiskHeapKB) + (uint)WhdLoadHeapKB(),
				SkipCoreConsistencyCheck = lp.Comm.CorePreferences.HasFlag(CoreComm.CorePreferencesFlags.WaterboxCoreConsistencyCheck),
				SkipMemoryConsistencyCheck = lp.Comm.CorePreferences.HasFlag(CoreComm.CorePreferencesFlags.WaterboxMemoryConsistencyCheck),
			}, [ _ledCallback, _cdTocCallback, _cdSectorCallback ]);

			_uae.SetCDCallbacks(_cdTocCallback, _cdSectorCallback);
			var (kickstartData, kickstartInfo) = CoreComm.CoreFileProvider.GetFirmwareWithGameInfoOrThrow(
				new(VSystemID.Raw.Amiga, _kickstartFirmwareId),
				"Firmware files are required!");
			if (_discAssets.Count > 0 && (_isCd32 || _isCdtv || _cdOnIde))
			{
				AppendSetting("cdimage0=CompactDisk0");
			}

			if (_cdOnIde)
			{
				// Gayle (A600/A1200) or the onboard controller (A4000); chipset_compatible already enables
				// this same IDE port, this is just belt-and-suspenders. The hard disk stays on UAE's own
				// virtual "uae0" controller (see MountHardDisk) so only the CD needs a real IDE unit here;
				// unit 1 (slave) keeps unit 0 free for a real IDE hard disk down the line.
				AppendSetting(_syncSettings.MachineConfig == MachineConfig.A4000_AGA_310_2M_8M ? "ide=a4000" : "ide=a600/a1200");
				AppendSetting("uaehf0=cd0,ro,CD0:,0,0,0,2048,0,,ide1");
			}

			if (_whdLoadArchives.Count > 0)
			{
				MountWhdLoad(kickstartData);
			}

			foreach (var hardDisk in _hardDisks)
			{
				var index = _mountedHardDisks.Count;
				MountHardDisk(GetFullName(hardDisk), hardDisk.FileData, $"DH{index}", 0);
			}

			for (var index = 0; index < _roms.Count; index++)
			{
				var rom = _roms[index];
				_exe.AddReadonlyFile(rom.FileData, FileNames.FD + index);
				if (index < _syncSettings.FloppyDrives)
				{
					_driveSlots[index] = index;
					AppendSetting($"floppy{index}={FileNames.FD}{index}");
					AppendSetting($"floppy{index}type={(int) DriveType.DRV_35_DD}");
					AppendSetting("floppy_write_protect=true");
				}
			}

			_exe.AddReadonlyFile(kickstartData, kickstartInfo.Name);
			filesToRemove.Add(kickstartInfo.Name);
			_args.AddRange(
			[ "-r", kickstartInfo.Name ]);

			if (_isCd32 && kickstartData.Length != 2 * 524288)
			{
				var (extendedKickstartData, extendedKickstartInfo) = CoreComm.CoreFileProvider.GetFirmwareWithGameInfoOrThrow(
					new(VSystemID.Raw.Amiga, "CD32ext"),
					"CD32 Extended ROM is required!");
				_exe.AddReadonlyFile(extendedKickstartData, extendedKickstartInfo.Name);
				filesToRemove.Add(extendedKickstartInfo.Name);
				_args.AddRange([ "-K", extendedKickstartInfo.Name ]);
			}

			if (_isCdtv)
			{
				var (cdtvExtData, cdtvExtInfo) = CoreComm.CoreFileProvider.GetFirmwareWithGameInfoOrThrow(
					new(VSystemID.Raw.Amiga, "CDTVext"),
					"CDTV Extended ROM is required!");
				_exe.AddReadonlyFile(cdtvExtData, cdtvExtInfo.Name);
				filesToRemove.Add(cdtvExtInfo.Name);
				_args.AddRange([ "-K", cdtvExtInfo.Name ]);
			}

			if (_isCd32 && _syncSettings.Cd32Fmv)
			{
				// cd32fmv=true only registers the cartridge board if p->cartfile is already set (cfgfile.c),
				// so cart_file must be appended first; both come after quickstart=CD32,0 (already in _args from
				// CreateArguments) so they are not reset by it.
				var (fmvData, fmvInfo) = CoreComm.CoreFileProvider.GetFirmwareWithGameInfoOrThrow(
					new(VSystemID.Raw.Amiga, "CD32FMV"),
					"CD32 Full Motion Video Cartridge ROM is required when the CD32 FMV Cartridge setting is enabled!");
				if (fmvData.Length == 2 * 262144)
				{
					// some dumps store the 256KB ROM twice back-to-back (both halves identical); the guest's
					// load_rom_rc call in cd32_fmv.c already caps the read at 262144 bytes, but truncate here
					// too so the file handed to the guest always matches the real ROM size regardless of that.
					var truncated = new byte[262144];
					Array.Copy(fmvData, truncated, truncated.Length);
					fmvData = truncated;
				}

				_exe.AddReadonlyFile(fmvData, fmvInfo.Name);
				filesToRemove.Add(fmvInfo.Name);
				AppendSetting($"cart_file={fmvInfo.Name}");
				AppendSetting("cd32fmv=true");
			}

			Console.WriteLine();
			Console.WriteLine(string.Join(" ", _args));
			Console.WriteLine();

			if (!_uae.Init(_args.Count, _args.ToArray()))
				throw new InvalidOperationException("Core rejected the rom!");

			_uae.SetCDVolume(_settings.CdVolume);

			foreach (var f in filesToRemove)
			{
				_exe.RemoveReadonlyFile(f);
			}

			PostInit();

			_uae.SetLEDCallback(!_isCd32 && !_isCdtv && _syncSettings.FloppyDrives > 0 ? _ledCallback : null);
		}

		/// <summary>WHDLoad needs Kickstart 2.0 or later; the KS1.3-class A500 and CDTV configs can't run it, so
		/// a WHDLoad pack silently falls back to the A1200 configuration instead (see <see cref="EffectiveMachineConfig"/>).</summary>
		private bool WhdLoadForcesA1200(MachineConfig config)
			=> _whdLoadArchives.Count > 0 && config is MachineConfig.A500_OCS_130_512K_512K or MachineConfig.CDTV;

		/// <summary>
		/// Decides how a loaded disc (if any) gets attached: CD32 or CDTV (quickstart machine, built-in CD
		/// controller), IDE (CD attached to the Gayle/A4000 IDE port alongside an existing hard disk or WHDLoad
		/// archive), or neither. See the <see cref="CdController"/> setting for the user-facing rule this implements.
		/// </summary>
		private (bool IsCd32, bool IsCdtv, bool CdOnIde) DetermineCdAttachment()
		{
			// an explicit CDTV selection always keeps its own built-in CD controller (and boots fine with no
			// disc loaded, i.e. the CDTV boot animation) unless WHDLoad bumps the machine to A1200 instead.
			if (_syncSettings.MachineConfig == MachineConfig.CDTV && !WhdLoadForcesA1200(_syncSettings.MachineConfig))
			{
				return (false, true, false);
			}

			if (_discAssets.Count == 0)
			{
				return (_syncSettings.MachineConfig == MachineConfig.CD32, false, false);
			}

			if (_syncSettings.MachineConfig == MachineConfig.CD32)
			{
				return (true, false, false);
			}

			switch (_syncSettings.CdController)
			{
				case CdController.CD32:
					return (true, false, false);

				case CdController.None:
					return (false, false, false);

				case CdController.IDE:
					if (!MachineHasIde(_syncSettings.MachineConfig))
					{
						throw new InvalidOperationException(
							"The CD controller setting is IDE, but the selected machine configuration has no IDE port "
							+ "(only A600, A1200 and A4000 do). Pick one of those machine configurations, or set the "
							+ "CD controller setting to Auto or CD32.");
					}

					return (false, false, true);

				default: // Auto: a plain CD keeps booting CD32 like before; a CD alongside a hard disk or WHDLoad
					// archive moves to IDE if (and only if) the user picked a machine that has an IDE port -
					// an A500 has none, so it stays on CD32 there.
					var hasOtherStorage = _hardDisks.Count > 0 || _whdLoadArchives.Count > 0;
					return hasOtherStorage && MachineHasIde(_syncSettings.MachineConfig)
						? (false, false, true)
						: (true, false, false);
			}
		}

		private static bool MachineHasIde(MachineConfig config)
			=> config is MachineConfig.A600_ECS_205_2M or MachineConfig.A1200_AGA_310_2M_8M or MachineConfig.A4000_AGA_310_2M_8M;

		private static bool IsHardDisk(IRomAsset rom)
			=> rom.Extension.Equals(".hdf", StringComparison.OrdinalIgnoreCase);

		private static bool IsWhdLoadArchive(IRomAsset rom)
			=> rom.Extension.Equals(".lha", StringComparison.OrdinalIgnoreCase)
				|| rom.Extension.Equals(".zip", StringComparison.OrdinalIgnoreCase);

		private static long HardDiskHeapKB(IRomAsset rom)
			=> (rom.FileData.LongLength + LibUAE.HARDDISK_CHUNK / 8) / 1024 + 64;

		private static bool HasRigidDiskBlock(byte[] data)
			=> data.Length >= 4 && data[0] == (byte)'R' && data[1] == (byte)'D' && data[2] == (byte)'S' && data[3] == (byte)'K';

		/// <param name="bootPriority">AmigaDOS boot priority; -128 marks the unit as not bootable</param>
		private void MountHardDisk(string name, byte[] data, string deviceName, int bootPriority)
		{
			var index = _mountedHardDisks.Count;
			if (data.LongLength < 512 || data.LongLength % 512 != 0)
			{
				throw new InvalidOperationException($"Hard disk image {index} is not a multiple of 512 bytes");
			}

			if (!_uae.HardDiskAlloc(index, data.LongLength))
			{
				throw new InvalidOperationException($"Core could not allocate hard disk image {index}");
			}

			const int step = 64 * 1024 * 1024;
			for (long offset = 0; offset < data.LongLength; offset += step)
			{
				var len = (int)Math.Min(step, data.LongLength - offset);
				if (!_uae.HardDiskWrite(index, offset, data, offset, len, false))
				{
					throw new InvalidOperationException($"Core rejected hard disk image {index}");
				}
			}

			// RDB images carry their own geometry; plain partition images get the classic 32/1/2 UAE layout
			var geometry = HasRigidDiskBlock(data) ? "0,0,0" : "32,1,2";
			AppendSetting($"hardfile2=rw,{deviceName}:{FileNames.HD}{index},{geometry},512,{bootPriority},,uae0");
			_mountedHardDisks.Add((name, data));
		}

		private static LibUAE.CDData CreateCDData(Disc disc)
		{
			var session = disc.Session1;
			var trackCount = session.InformationTrackCount;
			if (trackCount is < 1 or > LibUAE.CD_MAX_TRACKS)
			{
				throw new InvalidOperationException($"Unsupported CD track count: {trackCount}");
			}

			var data = new LibUAE.CDData
			{
				FirstTrack = 1,
				LastTrack = trackCount,
				Leadout = session.LeadoutLBA
			};

			for (var i = 0; i < trackCount; i++)
			{
				var track = session.Tracks[i + 1];
				var nextTrack = session.Tracks[i + 2];
				if (track.LBA < 0 || nextTrack.LBA <= track.LBA || track.Mode is < 0 or > 2)
				{
					throw new InvalidOperationException($"Invalid CD track {i + 1}");
				}

				data.Tracks[i] = new LibUAE.CDTrack
				{
					Start = track.LBA,
					End = nextTrack.LBA,
					Mode = track.Mode,
					Adr = 1,
					Control = (int)track.Control
				};
			}

			if (data.Leadout != data.Tracks[trackCount - 1].End)
			{
				throw new InvalidOperationException("CD leadout does not match the final track");
			}

			return data;
		}

		private void CDGetTOC(int disk, IntPtr dest)
		{
			if ((uint)disk < _cdTocs.Length)
			{
				Marshal.StructureToPtr(_cdTocs[disk], dest, false);
			}
		}

		private int CDRead(int disk, int lba, IntPtr dest, int sectorSize)
		{
			if ((uint)disk >= _cdReaders.Length || lba < 0 || sectorSize is not (2048 or 2352 or 2448))
			{
				return 0;
			}

			var buffer = new byte[sectorSize];
			int read;
			lock (_cdReaderLocks[disk])
			{
				read = sectorSize switch
				{
					2048 => _cdReaders[disk].ReadLBA_2048(lba, buffer, 0),
					2352 => _cdReaders[disk].ReadLBA_2352(lba, buffer, 0),
					2448 => _cdReaders[disk].ReadLBA_2448(lba, buffer, 0),
					_ => 0
				};
			}

			if (read != sectorSize)
			{
				return 0;
			}

			_exe.CopyFromExternal(buffer, dest);
			DriveLightOn = true;
			return read;
		}

		protected override LibWaterboxCore.FrameInfo FrameAdvancePrep(IController controller, bool render, bool rendersound)
		{
			DriveLightOn = false;
			var fi = new LibUAE.FrameInfo
			{
				Port1 = new LibUAE.ControllerState
				{
					Type = Ports[0],
					Buttons = LibUAE.AllButtons.None
				},
				Port2 = new LibUAE.ControllerState
				{
					Type = Ports[1],
					Buttons = LibUAE.AllButtons.None
				},
				Action = LibUAE.DriveAction.None
			};

			for (int port = 1; port <= 2; port++)
			{
				ref var currentPort = ref (port is 1 ? ref fi.Port1 : ref fi.Port2);

				switch (Ports[port - 1])
				{
					case LibUAE.ControllerType.DJoy:
						{
							foreach (var (name, button) in _joystickMap)
							{
								if (controller.IsPressed($"P{port} {Inputs.Joystick} {name}"))
								{
									currentPort.Buttons |= button;
								}
							}
							break;
						}
					case LibUAE.ControllerType.CD32Joy:
						{
							foreach (var (name, button) in _cd32padMap)
							{
								if (controller.IsPressed($"P{port} {Inputs.Cd32Pad} {name}"))
								{
									currentPort.Buttons |= button;
								}
							}
							break;
						}
					case LibUAE.ControllerType.Mouse:
						{
							if (controller.IsPressed($"P{port} {Inputs.MouseLeftButton}"))
							{
								currentPort.Buttons |= LibUAE.AllButtons.Button_1;
							}

							if (controller.IsPressed($"P{port} {Inputs.MouseRightButton}"))
							{
								currentPort.Buttons |= LibUAE.AllButtons.Button_2;
							}

							if (controller.IsPressed($"P{port} {Inputs.MouseMiddleButton}"))
							{
								currentPort.Buttons |= LibUAE.AllButtons.Button_3;
							}

							currentPort.MouseX = controller.AxisValue($"P{port} {Inputs.MouseX}");
							currentPort.MouseY = controller.AxisValue($"P{port} {Inputs.MouseY}");
							break;
						}
				}
			}

			if (_isCd32 || _isCdtv)
			{
				if (controller.IsPressed(Inputs.EjectDisk) && !_ejectPressed)
				{
					_uae.SetCDImage(-1);
					_currentDisc = -1;
					CoreComm.Notify("Ejected CD");
				}
				else if (controller.IsPressed(Inputs.InsertDisk) && !_insertPressed && _discAssets.Count > 0)
				{
					_currentDisc = _currentSlot;
					_uae.SetCDImage(_currentDisc);
					CoreComm.Notify($"Inserted CD: {GetFullName(_discAssets[_currentDisc])}");
				}

				if (controller.IsPressed(Inputs.NextSlot) && !_nextSlotPressed && _discAssets.Count > 0)
				{
					_currentSlot++;
					_currentSlot %= _discAssets.Count;
					CoreComm.Notify($"Selected CD slot {_currentSlot}: {GetFullName(_discAssets[_currentSlot])}");
				}
			}
			else if (_hasFloppies && controller.IsPressed(Inputs.EjectDisk))
			{
				if (!_ejectPressed)
				{
					fi.Action = LibUAE.DriveAction.EjectDisk;
					if (_driveSlots[_currentDrive] == _driveNullOrEmpty)
					{
						CoreComm.Notify($"Drive FD{_currentDrive} is already empty!", duration: 4);
					}
					else
					{
						CoreComm.Notify($"Ejected drive FD{_currentDrive}: {GetFullName(_roms[_driveSlots[_currentDrive]])}", duration: 4);
						_driveSlots[_currentDrive] = _driveNullOrEmpty;
					}
				}
			}
			else if (_hasFloppies && controller.IsPressed(Inputs.InsertDisk))
			{
				if (!_insertPressed)
				{
					fi.Action = LibUAE.DriveAction.InsertDisk;
					unsafe
					{
						var str = FileNames.FD + _currentSlot;
						fixed (char* filename = str)
						{
							fixed (byte* buffer = fi.Name.Buffer)
							{
								Encoding.ASCII.GetBytes(filename, str.Length, buffer, LibUAE.FILENAME_MAXLENGTH);
							}
						}
					}
					_driveSlots[_currentDrive] = _currentSlot;
					CoreComm.Notify($"Inserted drive FD{_currentDrive}: {GetFullName(_roms[_driveSlots[_currentDrive]])}", duration: 4);
				}
			}

			if (_hasFloppies && controller.IsPressed(Inputs.NextSlot))
			{
				if (!_nextSlotPressed)
				{
					_currentSlot++;
					_currentSlot %= _roms.Count;
					var selectedFile = _roms[_currentSlot];
					CoreComm.Notify($"Selected slot {_currentSlot}: {GetFullName(selectedFile)}", duration: 4);
				}
			}

			if (_hasFloppies && controller.IsPressed(Inputs.NextDrive))
			{
				if (!_nextDrivePressed)
				{
					_currentDrive++;
					_currentDrive %= _syncSettings.FloppyDrives;
					string name = "";
					if (_driveSlots[_currentDrive] == _driveNullOrEmpty)
					{
						name = "empty";
					}
					else
					{
						name = GetFullName(_roms[_driveSlots[_currentDrive]]);
					}
					CoreComm.Notify($"Selected drive FD{_currentDrive}: {name}", duration: 4);
				}
			}

			_ejectPressed = controller.IsPressed(Inputs.EjectDisk);
			_insertPressed = controller.IsPressed(Inputs.InsertDisk);
			_nextSlotPressed = controller.IsPressed(Inputs.NextSlot);
			_nextDrivePressed = controller.IsPressed(Inputs.NextDrive);
			fi.CurrentDrive = _currentDrive;

			if (controller.IsPressed(Inputs.Power))
			{
				fi.Reset = LibUAE.ResetAction.Hard;
			}
			else if (controller.IsPressed(Inputs.Reset))
			{
				fi.Reset = LibUAE.ResetAction.Soft;
			}

			foreach (var (name, key) in _keyboardMap)
			{
				if (controller.IsPressed(name))
				{
					unsafe
					{
						fi.Keys.Buffer[(int)key] = 1;
					}
				}
			}

			return fi;
		}

		protected override void FrameAdvancePost()
		{
			UpdateVideoStandard(false);
		}

		protected override void SaveStateBinaryInternal(BinaryWriter writer)
		{
			writer.Write(_ejectPressed);
			writer.Write(_insertPressed);
			writer.Write(_nextSlotPressed);
			writer.Write(_nextDrivePressed);
			writer.Write(_currentDrive);
			writer.Write(_currentSlot);
			writer.Write(_driveSlots[0]);
			writer.Write(_driveSlots[1]);
			writer.Write(_driveSlots[2]);
			writer.Write(_driveSlots[3]);
			writer.Write(_currentDisc);
		}

		protected override void LoadStateBinaryInternal(BinaryReader reader)
		{
			_ejectPressed = reader.ReadBoolean();
			_insertPressed = reader.ReadBoolean();
			_nextSlotPressed = reader.ReadBoolean();
			_nextDrivePressed = reader.ReadBoolean();
			_currentDrive = reader.ReadInt32();
			_currentSlot = reader.ReadInt32();
			_driveSlots[0] = reader.ReadInt32();
			_driveSlots[1] = reader.ReadInt32();
			_driveSlots[2] = reader.ReadInt32();
			_driveSlots[3] = reader.ReadInt32();
			_currentDisc = reader.ReadInt32();
			if (_isCd32 || _isCdtv)
			{
				_uae.SetCDImage(_currentDisc);
			}

			// the volume lives in the guest's prefs, so the state just restored carries whatever it was at save time
			_uae.SetCDVolume(_settings.CdVolume);
		}

		private void UpdateVideoStandard(bool initial)
		{
			var ntsc = initial
				? _syncSettings.Region is VideoStandard.NTSC
				: BufferHeight == LibUAE.NTSC_HEIGHT;

			if (ntsc)
			{
				_correctedWidth = LibUAE.PAL_WIDTH * 6 / 7;
				VsyncNumerator = LibUAE.VIDEO_NUMERATOR_NTSC;
				VsyncDenominator = LibUAE.VIDEO_DENOMINATOR_NTSC;
			}
			else
			{
				_correctedWidth = LibUAE.PAL_WIDTH;
				VsyncNumerator = LibUAE.VIDEO_NUMERATOR_PAL;
				VsyncDenominator = LibUAE.VIDEO_DENOMINATOR_PAL;
			}
		}

		private static class FileNames
		{
			public const string FD = "FloppyDisk";
			public const string CD = "CompactDisk";
			public const string HD = "HardDrive";
		}
	}
}