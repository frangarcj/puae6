from pathlib import Path
import sys

# tools/ holds prepare_whdload.py and the whdload package (lha.py, ffs.py): the tests import them by name, the way the command line runs them
TOOLS = str(Path(__file__).resolve().parents[2] / "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
