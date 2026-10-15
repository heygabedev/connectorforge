"""Scan tracked files without printing discovered secret values."""

import subprocess
from pathlib import Path

from detect_secrets.core.scan import scan_file
from detect_secrets.settings import default_settings

files = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
failed = False
with default_settings():
    for name in files:
        if not name or not Path(name).is_file() or name.endswith((".png", ".lock", "-lock.json")):
            continue
        for finding in scan_file(name):
            print(f"Potential secret: {name}:{finding.line_number} ({finding.type})")
            failed = True
if failed:
    raise SystemExit(1)
