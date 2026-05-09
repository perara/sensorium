#!/usr/bin/env python3
from pathlib import Path
import sys


src_dir = Path(__file__).resolve().parents[2] / "src"
if src_dir.exists():
    sys.path.insert(0, str(src_dir))

from sensorium.tools.verify_runtime_abi import main


if __name__ == "__main__":
    raise SystemExit(main())
