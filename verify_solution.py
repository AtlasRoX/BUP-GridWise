"""
GridWise Solution Verification Script
Runs all 10 public sample cases and prints a scored evaluation summary.
Usage:
    python verify_solution.py [--base-url http://localhost:8000]
"""

import argparse
import json
import os
import sys
import httpx
from app.main import app

SAMPLE_FILE = os.path.join(os.path.dirname(__file__), "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json")


def load_sample_cases():
    with open(SAMPLE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)["cases"]


async def run_verification(base_url: str = None):
    cases = load_sample_cases()
    print("=" * 80)
    print(f"Running GridWise Verification on {len(cases)} Public Sample Cases")
    if base_url:
        print(f"Target: Live HTTP Service at {base_url}")
        client = httpx.AsyncClient(base_url=base_url, timeout=30.0)
    else:
        print("Target: In-Process ASGI Test Client (no external server required)")
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30.0)

    try:
        # Check /health
        health_resp = await client.get("/health")
        if health_resp.status_code != 200 or health_resp.json().get("status") != "ok":
            print(f"[-] /health check failed! Status: {health_resp.status_code}, Body: {health_resp.text}")
            return False
        print("[+] /health check PASSED (status: ok)\n")

        print(f"{'Case ID':<12} {'Status':<10} {'Team Cost (BDT)':<18} {'Ref Cost (BDT)':<18} {'Quality Ratio':<15}")
        print("-" * 80)

        all_passed = True
        quality_ratios = []

        for case in cases:
            case_id = case["id"]
            case_input = case["input"]
            expected = case["expected_output"]
            ref_cost = expected["total_cost_bdt"]

            resp = await client.post("/optimize-energy", json=case_input)
            if resp.status_code != 200:
                print(f"{case_id:<12} {'FAILED':<10} {'HTTP ' + str(resp.status_code):<18} {ref_cost:<18} {'0.0000':<15}")
                all_passed = False
                continue

            data = resp.json()
            team_cost = data["total_cost_bdt"]
            q_ratio = min(1.0, ref_cost / team_cost) if team_cost > 0 else 0.0
            quality_ratios.append(q_ratio)

            # Check directive interpretations
            interp_ok = len(data["directive_interpretation"]) == len(expected["directive_interpretation"])
            for i, exp_interp in enumerate(expected["directive_interpretation"]):
                act_interp = data["directive_interpretation"][i]
                if act_interp["applies"] != exp_interp["applies"] or act_interp["directive_type"] != exp_interp["directive_type"]:
                    interp_ok = False

            status_str = "PASS" if (q_ratio >= 0.99 and interp_ok) else "MISMATCH"
            if status_str != "PASS":
                all_passed = False

            print(f"{case_id:<12} {status_str:<10} {team_cost:<18.2f} {ref_cost:<18.2f} {q_ratio:<15.4f}")

        print("-" * 80)
        avg_quality = sum(quality_ratios) / len(quality_ratios) if quality_ratios else 0.0
        opt_score = avg_quality * 10.0
        print(f"Average Quality Ratio: {avg_quality:.4f} | Optimization Score: {opt_score:.2f} / 10.00")
        print(f"Overall Result: {'ALL CHECKS PASSED' if all_passed else 'SOME CHECKS FAILED'}")
        print("=" * 80)
        return all_passed
    finally:
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GridWise Solution Verifier")
    parser.add_argument("--base-url", type=str, default=None, help="Base URL of live deployed service")
    args = parser.parse_args()

    import asyncio
    success = asyncio.run(run_verification(args.base_url))
    sys.exit(0 if success else 1)
