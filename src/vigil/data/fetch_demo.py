"""Download the open-access MIMIC-IV-ED demo subset (~70 KB, no credentialing).

    python -m vigil.data.fetch_demo
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

BASE = "https://physionet.org/files/mimic-iv-ed-demo/2.2/ed"
TABLES = ["edstays", "triage", "vitalsign", "medrecon", "pyxis", "diagnosis"]
DEST = Path("data/raw/mimic-iv-ed-demo/ed")


def main(dest: Path = DEST) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    for t in TABLES:
        target = dest / f"{t}.csv.gz"
        if target.exists():
            print(f"  have {t}")
            continue
        print(f"  fetching {t} ...", end=" ", flush=True)
        urllib.request.urlretrieve(f"{BASE}/{t}.csv.gz", target)
        print(f"{target.stat().st_size / 1024:.0f} KB")
    print(f"\nDemo subset ready in {dest}")
    print("Licence: ODbL. Open access - but still never commit it (see .gitignore).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
