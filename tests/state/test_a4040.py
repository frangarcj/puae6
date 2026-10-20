"""T55c native A4040: public frame/state ABI, actual 040/060 and internal FPU.

Frontend selectors, real firmware and the Waterbox gate remain in the parent.
"""
import struct
from tests.state import test_det02 as d
from tests.state import test_a4030 as a30
from tests.state import test_z3 as z3
from tests.state import test_state_a600 as a600
from tests.state.harness import FieldTable
from tests.host import fpu_asm as f

CPU = {"68040": "80008000", "68060": "F8E0E000"}
FPU = """entry:
    MOVE.W #$2700,SR
    LEA $7000.W,A7
    MOVE.B #3,$BFE201.L
    MOVE.B #2,$BFE001.L
    MOVEQ #1,D0
    .word $F200,$4000
    MOVEQ #3,D0
    .word $F200,$4080
    .word $F200,$0420           ; FP0 = 1/3, nearest extended
    LEA $3000.W,A0
loop:
""" + "\n".join((f.cpgen_reg("fmove", 0, 2), f.cpgen_reg("fadd", 0, 2),
    f.fmove_out(f.EXTENDED, 0, f.abs_long(0x2020)), f.fsave(f.ind(0)), f.frestore(f.ind(0)))) + """
    ADDQ.L #1,D2
    MOVE.L D2,$2000.L
    BRA.S loop
"""


class A4040Tests(d.Det02Case):
    MACHINE = "a4040"
    SAVE_OP, LOAD_OP = "--public-save-at", "--public-load-at"
    ROMS = {**{"cpu-"+cpu: (a30.PROGRAM.replace("#$3113", "#$"+cacr), None, None)
               for cpu, cacr in CPU.items()}, "fpu": (FPU, None, None), "z3": (z3.program(True), None, None)}

    @classmethod
    def probe_on(cls, machine, rom, *args):
        return super().probe_on(machine, rom, *args, "--cpu-compatibility", "normal")

    def test_real_cpu_controls_line_f_and_public_restore(self):
        for cpu, cacr in CPU.items():
            with self.subTest(cpu=cpu):
                name="cpu-"+cpu
                state, ram = self.dir/(name+".state"), self.dir/(name+".ram")
                run=self.probe(name, "--cpu", cpu, "--units", "3", "--save-fields-at", "3", str(state),
                               "--ramout-at", "3", "0x2000", "20", str(ram))
                self.assertFalse(run.save_errors)
                resumed, fline, alias=struct.unpack_from(">III", ram.read_bytes())
                self.assertGreater(min(resumed,fline),0)
                self.assertLessEqual(abs(resumed-fline),1)
                self.assertEqual(0x13572468,alias)
                self.assertEqual(bytes([15,0x96,0x7f,10]),ram.read_bytes()[16:20])
                table=FieldTable(state.read_bytes(),0,run.fields)
                self.assertEqual(int(cacr,16),table.get("cpu.cacr"))
                memory=FieldTable(state.read_bytes(),2,run.fields)
                self.assertEqual(0x96,memory.get("mbres.ramsey_config"))
                for mode in (d.SAME,d.NEW):
                    self.assertIsNone(self.first_divergence(name,["--cpu",cpu],3,mode))

    def test_no_fast_ram_preserves_motherboard_and_empty_ide(self):
        for cpu in CPU:
            name="cpu-"+cpu
            extra=["--cpu",cpu,"--fast","4294967295"]
            state=self.dir/(cpu+"-no-fast.state")
            run=self.probe(name,*extra,"--units","3","--save-fields-at","3",str(state))
            self.assertFalse(run.save_errors)
            memory=FieldTable(state.read_bytes(),2,run.fields)
            self.assertNotIn("ram.fast_size",memory.where)
            self.assertEqual(0x96,memory.get("mbres.ramsey_config"))
            for mode in (d.SAME,d.NEW):
                self.assertIsNone(self.first_divergence(name,extra,3,mode))

    def test_internal_fpu_result_idle_frames_and_public_restore(self):
        for cpu in CPU:
            with self.subTest(cpu=cpu):
                ram=self.dir/(cpu+".fpu.ram")
                extra=["--cpu",cpu,"--fpu",cpu]
                self.probe("fpu",*extra,"--units","3","--ramout-at","3","0x2020","12",str(ram))
                # IEEE extended 1/3, independently rounded; memory has a pad word.
                self.assertEqual((0x3ffd,0xaaaaaaaa,0xaaaaaaab),struct.unpack(">H2xII",ram.read_bytes()))
                for mode in (d.SAME,d.NEW):
                    self.assertIsNone(self.first_divergence("fpu",extra,3,mode))

    def test_identity_separates_internal_fpu_and_cpu_and_normalizes_auto(self):
        states=[];hashes=[]
        for cpu,fpu in (("68040","0"),("68040","68040"),("68060","0"),("68060","68060")):
            path=self.dir/(cpu+"-"+fpu+".state")
            self.probe("cpu-"+cpu,"--cpu",cpu,"--fpu",fpu,"--units","3",self.SAVE_OP,"3",str(path))
            hashes.append(path.read_bytes()[a600.CONFIG_HASH]);states.append(path)
        self.assertEqual(4,len(set(hashes)))
        for i,(cpu,fpu) in enumerate((("68040","0"),("68040","68040"),("68060","0"),("68060","68060"))):
            run=self.probe("cpu-"+cpu,"--cpu",cpu,"--fpu",fpu,"--units","1",self.LOAD_OP,"0",str(states[(i+1)%4]))
            self.assertEqual([(0,10)],[(u,c) for u,c,_ in run.loads])
        auto=self.dir/"auto.state"
        self.probe("cpu-68040","--units","3",self.SAVE_OP,"3",str(auto))
        self.assertEqual(hashes[0],auto.read_bytes()[a600.CONFIG_HASH])

    def test_z3_physical_board_and_restore_on_both_cpus(self):
        for cpu in CPU:
            extra=["--cpu",cpu,"--z3","16"]
            ram=self.dir/(cpu+".z3.ram")
            self.probe("z3",*extra,"--units","12","--ramout-at","12","0x2000","12",str(ram))
            self.assertEqual((0x11223344,0x55667788,0x13572468),struct.unpack(">III",ram.read_bytes()))
            for mode in (d.SAME,d.NEW):
                self.assertIsNone(self.first_divergence("z3",extra,8,mode))
