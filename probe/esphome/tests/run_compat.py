#!/usr/bin/env python3
"""Compile the C++ engine for the host and diff it against the Python probe.

For every scenario in scenarios/ the same scripted wire conversation is played
through (a) the integration's own Python ``homeplug`` package behind
``probe/powerline_probe.py`` and (b) the C++ engine in
``components/powerline_probe``. The JSON each ``call`` returns must be equal.

    python probe/esphome/tests/run_compat.py            # needs g++
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMP = HERE.parent / "components" / "powerline_probe"


def main() -> int:
    gxx = shutil.which("g++") or shutil.which("c++")
    if not gxx:
        print("no C++ compiler found")
        return 2
    subprocess.run([sys.executable, str(HERE / "make_scenarios.py")], check=True, stdout=subprocess.DEVNULL)
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / "host_test"
        subprocess.run([gxx, "-std=c++17", "-Wall", "-Wextra", "-O1", f"-I{COMP}", str(HERE / "host_test.cpp"),
                        str(COMP / "homeplug.cpp"), "-o", str(exe)], check=True)
        failed = 0
        for scenario in sorted((HERE / "scenarios").glob("*.txt")):
            cpp = subprocess.run([str(exe), str(scenario)], capture_output=True, text=True, check=True)
            py = subprocess.run([sys.executable, "-I", str(HERE / "ref_python.py"), str(scenario)],
                                capture_output=True, text=True)
            if py.returncode != 0:
                print(f"FAIL {scenario.name}: python reference crashed\n{py.stderr}")
                failed += 1
                continue
            a = [json.loads(line) for line in cpp.stdout.splitlines()]
            b = [json.loads(line) for line in py.stdout.splitlines()]
            if a == b:
                print(f"ok   {scenario.name}  ({len(a)} call(s))")
            else:
                failed += 1
                print(f"FAIL {scenario.name}")
                for i, (x, y) in enumerate(zip(a, b)):
                    if x != y:
                        print(f"  call {i} C++   : {json.dumps(x, sort_keys=True)}")
                        print(f"  call {i} Python: {json.dumps(y, sort_keys=True)}")
                if len(a) != len(b):
                    print(f"  call count C++ {len(a)} vs Python {len(b)}")
        print("all scenarios match" if not failed else f"{failed} scenario(s) differ")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
