import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                       # openrouter_mockserver package
sys.path.insert(0, str(ROOT / "package" / "bin"))   # add-on modules
