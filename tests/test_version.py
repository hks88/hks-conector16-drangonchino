from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

import compilar


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VersionConsistencyTests(unittest.TestCase):
    def test_visible_version_matches_version_file(self):
        expected = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        frontend_source = (PROJECT_ROOT / "frontend.py").read_text(encoding="utf-8")
        match = re.search(r'^VERSION_LOCAL\s*=\s*["\']([^"\']+)["\']', frontend_source, re.MULTILINE)

        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), expected)

    def test_readme_declares_current_version(self):
        expected = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn(f"Versión incluida: **{expected}**", readme)

    def test_compiler_requires_win32timezone_for_native_result_lookup(self):
        compiler_source = (PROJECT_ROOT / "compilar.py").read_text(encoding="utf-8")
        self.assertIn('"--hidden-import", "win32timezone"', compiler_source)
        self.assertIn("El ejecutable no incluyó win32timezone/win32file", compiler_source)

    def test_compiler_accepts_matching_visible_version(self):
        expected = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        with tempfile.TemporaryDirectory() as temp_dir:
            frontend = Path(temp_dir) / "frontend.py"
            frontend.write_text(
                f'VERSION_LOCAL = "{expected}"\n',
                encoding="utf-8",
            )

            self.assertEqual(
                compilar.validate_version_consistency(expected, frontend),
                expected,
            )

    def test_compiler_rejects_mismatched_visible_version(self):
        expected = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
        with tempfile.TemporaryDirectory() as temp_dir:
            frontend = Path(temp_dir) / "frontend.py"
            frontend.write_text('VERSION_LOCAL = "0.0.0"\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "deben coincidir"):
                compilar.validate_version_consistency(expected, frontend)


if __name__ == "__main__":
    unittest.main()
