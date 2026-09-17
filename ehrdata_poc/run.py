"""Run the pipeline end to end, excluding the exploration notebooks."""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

for script in ("build_dataset.py", "federate.py"):
    print(f"\n=== {script} ===", flush=True)
    subprocess.run([sys.executable, script], cwd=HERE, check=True)
