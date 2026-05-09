#!/usr/bin/env python3
from pathlib import Path
import sys


sys.dont_write_bytecode = True
src_dir = Path(__file__).resolve().parents[2] / "src"
if src_dir.exists():
    sys.path.insert(0, str(src_dir))

from sensorium.tools.compute_sync_manifest import main


if __name__ == "__main__":
    raise SystemExit(main())
