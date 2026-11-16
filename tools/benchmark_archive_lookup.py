"""Compara búsqueda Python y filtro nativo en un archivo histórico simulado.

Solo crea archivos vacíos dentro de una carpeta temporal. No usa el Agente FF,
Odoo, PAC, carpetas de clientes ni documentos fiscales reales.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from local_fiscal import matching_files  # noqa: E402


def measure(operation, repetitions: int) -> dict:
    samples = []
    result_count = None
    for _ in range(repetitions):
        started = time.perf_counter()
        result_count = len(operation())
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "samples": repetitions,
        "result_count": result_count,
        "median_ms": round(statistics.median(samples), 3),
        "max_ms": round(max(samples), 3),
    }


def benchmark(file_count: int, repetitions: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="ff_archive_SIMULADO_") as directory:
        archive = Path(directory) / "Resultado_FF"
        archive.mkdir()
        for index in range(file_count):
            (archive / f"result_{7000000000 + index:010d}_1.txt").touch()
        target = archive / "result_9981010243_1.txt"
        target.touch()

        miss = "result_0000000000_*"
        hit = "result_9981010243_*"
        report = {
            "scope": "SIMULADO; carpeta temporal, sin Agente FF, Odoo, PAC ni datos reales",
            "historical_files": file_count + 1,
            "python_path_glob_miss": measure(
                lambda: [path for path in archive.glob(miss) if path.is_file()],
                repetitions,
            ),
            "windows_native_filter_miss": measure(
                lambda: matching_files(archive, miss),
                repetitions,
            ),
            "python_path_glob_hit": measure(
                lambda: [path for path in archive.glob(hit) if path.is_file()],
                repetitions,
            ),
            "windows_native_filter_hit": measure(
                lambda: matching_files(archive, hit),
                repetitions,
            ),
        }
        if report["windows_native_filter_hit"]["result_count"] != 1:
            raise AssertionError("El filtro nativo no encontró el resultado simulado")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=20000)
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()
    if args.files < 1 or args.repetitions < 1:
        parser.error("--files y --repetitions deben ser positivos")
    print(json.dumps(benchmark(args.files, args.repetitions), indent=2))
