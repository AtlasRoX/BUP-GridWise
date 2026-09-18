"""
GridWise Latency and Reliability Benchmark
Measures HTTP request latency percentiles (min, p50, p90, p95, p99, max)
and failure rates across sequential and concurrent requests.

Usage:
    python benchmark_latency.py [--base-url http://localhost:8000] [--requests 30] [--concurrency 1]
"""

import argparse
import asyncio
import statistics
import time
import httpx
from app.main import app

BENCHMARK_PAYLOAD = {
    "scenario_id": "BENCH_01",
    "operator_notes": ["Solar panels operate at 30% from 11 to 14 due to cloud cover."],
    "hours": [
        {
            "hour": h,
            "demand_kwh": 80.0 + (20.0 if 17 <= h <= 21 else 0.0),
            "solar_kwh": 40.0 if 9 <= h <= 15 else 0.0,
            "tariff_bdt_per_kwh": 6.0 if h < 6 or h >= 22 else (18.0 if 17 <= h <= 21 else 10.0),
        }
        for h in range(24)
    ],
    "battery": {
        "capacity_kwh": 200.0,
        "initial_energy_kwh": 100.0,
        "minimum_energy_kwh": 40.0,
        "max_charge_kwh_per_hour": 50.0,
        "max_discharge_kwh_per_hour": 50.0,
    },
}


async def send_single_request(client: httpx.AsyncClient) -> tuple[float, bool, str]:
    start = time.perf_counter()
    try:
        resp = await client.post("/optimize-energy", json=BENCHMARK_PAYLOAD)
        elapsed = time.perf_counter() - start
        if resp.status_code == 200:
            return elapsed, True, "OK"
        else:
            return elapsed, False, f"HTTP {resp.status_code}"
    except Exception as e:
        elapsed = time.perf_counter() - start
        return elapsed, False, str(type(e).__name__)


async def run_benchmark(base_url: str = None, num_requests: int = 30, concurrency: int = 1):
    print("=" * 80)
    print("GRIDWISE LATENCY AND PERFORMANCE BENCHMARK")
    print(f"Requests: {num_requests} | Concurrency: {concurrency}")
    if base_url:
        print(f"Target: Live HTTP Service at {base_url}")
        client = httpx.AsyncClient(base_url=base_url, timeout=45.0)
    else:
        print("Target: In-Process ASGI Application")
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://test", timeout=45.0)

    try:
        # Warm-up phase
        print("Warming up (2 requests)...", end="", flush=True)
        for _ in range(2):
            await send_single_request(client)
        print(" done.\n")

        print(f"Executing {num_requests} benchmark requests...")
        durations = []
        failures = 0
        failure_reasons = []

        semaphore = asyncio.Semaphore(concurrency)

        async def worker():
            nonlocal failures
            async with semaphore:
                dur, ok, reason = await send_single_request(client)
                durations.append(dur)
                if not ok:
                    failures += 1
                    failure_reasons.append(reason)
                print(".", end="", flush=True)

        tasks = [worker() for _ in range(num_requests)]
        bench_start = time.perf_counter()
        await asyncio.gather(*tasks)
        total_wall_time = time.perf_counter() - bench_start
        print("\n")

        # Compute percentiles with numpy for statistical accuracy
        import numpy as np
        count = len(durations)
        min_lat = float(np.min(durations))
        max_lat = float(np.max(durations))
        mean_lat = float(np.mean(durations))
        p50 = float(np.percentile(durations, 50))
        p90 = float(np.percentile(durations, 90))
        p95 = float(np.percentile(durations, 95))
        p99 = float(np.percentile(durations, 99))

        print("-" * 80)
        print(f"{'Metric':<30} {'Value':<20} {'Target / Requirement'}")
        print("-" * 80)
        print(f"{'Total Requests':<30} {count:<20} {num_requests}")
        print(f"{'Successful Requests':<30} {count - failures:<20} 100%")
        print(f"{'Failed Requests':<30} {failures:<20} 0")
        print(f"{'Failure Rate':<30} {failures / count * 100:.2f}%{'':<14} 0.00%")
        print(f"{'Throughput (req/sec)':<30} {count / total_wall_time:.2f}{'':<16} -")
        print(f"{'Min Latency':<30} {min_lat:.3f} s")
        print(f"{'Mean Latency':<30} {mean_lat:.3f} s")
        print(f"{'p50 (Median) Latency':<30} {p50:.3f} s{'':<13} < 3.0 s")
        print(f"{'p90 Latency':<30} {p90:.3f} s{'':<13} < 5.0 s")
        print(f"{'p95 Latency':<30} {p95:.3f} s{'':<13} <= 5.0 s (Full Score)")
        print(f"{'p99 Latency':<30} {p99:.3f} s{'':<13} < 10.0 s")
        print(f"{'Max Latency':<30} {max_lat:.3f} s{'':<13} < 29.0 s (Organizer Ceiling: 30s)")
        print("-" * 80)

        p95_pass = p95 <= 5.0
        max_pass = max_lat < 29.0
        print(f"Latency P95 Evaluation: {'FULL-SCORE COMPLIANT (<= 5.0s)' if p95_pass else f'OUTSIDE FULL-SCORE BAND ({p95:.3f}s > 5.0s)'}")
        print(f"30-Second Limit Compliance: {'FULLY COMPLIANT (<29s)' if max_pass else 'VIOLATED'}")
        print("=" * 80)

    finally:
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GridWise Latency Benchmark")
    parser.add_argument("--base-url", type=str, default=None, help="Base URL of service")
    parser.add_argument("--requests", type=int, default=30, help="Number of requests")
    parser.add_argument("--concurrency", type=int, default=1, help="Concurrent requests")
    args = parser.parse_args()

    asyncio.run(run_benchmark(args.base_url, args.requests, args.concurrency))
