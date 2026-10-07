"""Wall time of a unittest target split into time inside subprocess.run (probe and other children) and the rest (Python)."""
import subprocess, sys, time, unittest, resource
spent = {"probe": 0.0, "other": 0.0, "calls": 0}
real = subprocess.run
def timed(args, *a, **k):
    t = time.monotonic()
    try:
        return real(args, *a, **k)
    finally:
        d = time.monotonic() - t
        first = str(args[0]) if isinstance(args, (list, tuple)) else str(args)
        spent["probe" if "p6_state_probe" in first else "other"] += d
        spent["calls"] += 1
subprocess.run = timed
sys.path.insert(0, ".")
t0 = time.monotonic()
suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:])
result = unittest.TextTestRunner(verbosity=0).run(suite)
wall = time.monotonic() - t0
ru = resource.getrusage(resource.RUSAGE_CHILDREN)
print(f"MEASURE targets={sys.argv[1:]} wall={wall:.1f}s probe={spent['probe']:.1f}s other_children={spent['other']:.1f}s python={wall-spent['probe']-spent['other']:.1f}s calls={spent['calls']} tests={result.testsRun} children_max_rss_mb={ru.ru_maxrss/1048576:.0f}")
