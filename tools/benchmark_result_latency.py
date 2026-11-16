"""Mide costos locales del protocolo fiscal sin tocar el Agente FF real.

La prueba usa carpetas y una base SQLite temporales. Los resultados permiten
separar el costo del conector del tiempo que tarda un Agente externo en crear
``result_NUMERO_n.txt``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from fiscal_sequence import FiscalStore  # noqa: E402
from local_fiscal import find_result, publish_once  # noqa: E402
import connector_logging  # noqa: E402
import order_process  # noqa: E402
from test_fiscal_sequence import SCOPE, payload  # noqa: E402


def milliseconds(seconds: float) -> float:
    return round(seconds * 1000, 3)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def summarize(values: list[float]) -> dict:
    return {
        "samples": len(values),
        "median_ms": milliseconds(statistics.median(values)),
        "p95_ms": milliseconds(percentile(values, 0.95)),
        "max_ms": milliseconds(max(values)),
    }


def benchmark(poll_interval: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="ff_latency_SIMULADO_") as directory:
        root = Path(directory)
        connector_logging.configure_logging(
            root / "benchmark.log",
            force=True,
            console=False,
        )
        agent = root / "Agente_FF"
        results = agent / "Resultado_FF"
        agent.mkdir()
        results.mkdir()

        publication_times = []
        for index in range(50):
            start = time.perf_counter()
            publish_once(agent / f"{index + 1:010d}.txt", b"L01|prueba\nL07|fin")
            publication_times.append(time.perf_counter() - start)

        state_path = root / "fiscal_state.sqlite3"
        store = FiscalStore.initialize(state_path, SCOPE, 9981010242, 9517020262)
        sqlite_times = []
        for index in range(1, 21):
            request = payload(index)
            start = time.perf_counter()
            record = store.prepare(
                request,
                lambda data, valid, refund: order_process.render_document(
                    data,
                    valid,
                    refund,
                    fiscal_scope=SCOPE,
                ),
            )
            store.claim_dispatch(record["order_uid"])
            store.save_response(record["order_uid"], {"procesado": True})
            sqlite_times.append(time.perf_counter() - start)

        detection_overhead = []
        total_waits = []
        # Los distintos desfases ejercitan los peores y mejores puntos del poll.
        delays = [0.011, 0.063, 0.127, 0.189, 0.241] * 4
        for index, delay in enumerate(delays, start=1):
            number = f"{9000000000 + index:010d}"
            # El Agente publica primero en la carpeta activa. Resultado_FF es
            # histórico y el watcher no mueve una respuesta registrada como
            # activa hasta que termina este ciclo.
            response_path = agent / f"result_{number}_1.txt"

            def create_response() -> None:
                time.sleep(delay)
                response_path.write_text(
                    "CUFE-SIMULADO\nhttps://example.test/cafe\n2026-08-29T12:00:00-05:00\n",
                    encoding="utf-8",
                )

            worker = threading.Thread(target=create_response)
            start = time.perf_counter()
            worker.start()
            found = find_result(
                number,
                agent,
                results,
                timeout=2,
                poll_interval=poll_interval,
                search_archive=False,
            )
            elapsed = time.perf_counter() - start
            worker.join()
            if not found or not found.get("procesado"):
                raise AssertionError(f"No se detectó la respuesta simulada {number}")
            total_waits.append(elapsed)
            detection_overhead.append(max(0.0, elapsed - delay))

        report = {
            "scope": "SIMULADO; no usa Agente FF, PAC, Odoo ni documentos reales",
            "result_poll_interval_ms": milliseconds(poll_interval),
            "atomic_txt_publication": summarize(publication_times),
            "sqlite_prepare_claim_save": summarize(sqlite_times),
            "result_poll_extra_over_known_agent_delay": summarize(detection_overhead),
            "result_wait_total": summarize(total_waits),
        }
        # Cierra el handler dentro del temporal antes de que Windows elimine la
        # carpeta. El nuevo handler vive fuera de ella y el proceso termina aquí.
        connector_logging.configure_logging(force=True, console=False)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-interval", type=float, default=0.05)
    args = parser.parse_args()
    if not 0 < args.poll_interval <= 1:
        parser.error("--poll-interval debe ser mayor que 0 y menor o igual que 1")
    print(json.dumps(benchmark(args.poll_interval), indent=2))
