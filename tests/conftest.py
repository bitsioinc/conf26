import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))                          # mockserver pkg
sys.path.insert(0, str(Path(__file__).parent.parent / "TA_anthropic" / "package" / "bin"))
