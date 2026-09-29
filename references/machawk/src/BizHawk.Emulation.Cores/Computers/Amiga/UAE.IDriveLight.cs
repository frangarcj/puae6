using BizHawk.Emulation.Common;

namespace BizHawk.Emulation.Cores.Computers.Amiga
{
	public partial class UAE : IDriveLight
	{
		public bool DriveLightEnabled { get; }
		public bool DriveLightOn { get; private set; }
		public string DriveLightIconDescription => _isCd32 || _isCdtv || _cdOnIde ? "CD Drive Activity" : "Floppy Drive Activity";
	}
}