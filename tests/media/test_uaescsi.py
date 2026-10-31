"""T84g1: cooperative uaescsi.device backend and its bounded MMC surface."""
import json
import subprocess

from tests.media import test_cd_device as fixture


class UaeScsiTests(fixture.CdDeviceTests):
    PROBE = fixture.ROOT / "tests/media/uaescsi_probe.cpp"

    def test_engine_object_has_no_host_worker_or_physical_scsi_dependency(self):
        subprocess.run(
            ["cmake", "--build", str(fixture.ROOT / "build/link-audit"), "--target", "p6_headless", "-j2"],
            check=True,
            timeout=600,
        )
        manifest = json.loads((fixture.ROOT / "manifests/source-manifest.json").read_text())
        units = manifest["selection"]["upstream_units"]
        self.assertIn("scsiemul.cpp", units)
        self.assertNotIn("scsidev.cpp", units)

        obj = fixture.ROOT / "build/link-audit/CMakeFiles/p6_engine.dir/build/upstream/scsiemul.cpp.o"
        undefined = subprocess.run(["nm", "-u", str(obj)], check=True, capture_output=True, text=True).stdout
        for forbidden in ("uae_start_thread", "uae_sem_", "comm_pipe", "trap_background", "device_func_init"):
            self.assertNotIn(forbidden, undefined)
        linked = subprocess.run(
            ["nm", str(fixture.ROOT / "build/link-audit/p6_headless")],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertIn("scsidev_install", linked)
        self.assertIn("sys_command_scsi_direct_native", linked)
