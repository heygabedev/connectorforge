"""Enforce branch coverage for the security boundary and available core modules."""

import json
from pathlib import Path

report = json.loads(Path("coverage.json").read_text())
required = {"security.py", "compiler.py", "runtime.py", "releases.py"}
failures = []
for name, result in report["files"].items():
    if Path(name).name not in required:
        continue
    summary = result["summary"]
    branches = summary["num_branches"]
    percent = 100 * summary["covered_branches"] / branches if branches else 100
    print(f"{name}: {percent:.1f}% branch coverage")
    if percent < 90:
        failures.append(name)
if failures:
    raise SystemExit("Branch coverage below 90%: " + ", ".join(failures))
