"""Verifica dependencias dentro del EXE PyInstaller real, sin ejecutarlo."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
executable = ROOT / "dist" / "Conector_V5.2.8.exe"
result = subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller.utils.cliutils.archive_viewer",
        "-r",
        "-l",
        str(executable),
    ],
    capture_output=True,
    text=True,
    timeout=60,
)
assert result.returncode == 0, result.stderr
assert "'win32timezone'" in result.stdout
assert "win32file.pyd" in result.stdout
report = {
    "success": True,
    "executable": executable.name,
    "scope": "Inspección recursiva del archivo PyInstaller; el EXE no se ejecutó",
    "embedded_modules": ["win32timezone", "win32/win32file.pyd"],
    "runtime_fallback_test": "test_missing_win32_transitive_dependency_falls_back_without_failing_sale",
}
(ROOT / "test-packaged-imports-results.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(report, ensure_ascii=True, indent=2))
