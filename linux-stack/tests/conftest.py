import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent.parent))       # linux-stack/
sys.path.insert(0, str(_ROOT / "audit-service"))            # audit-service/logger.py
