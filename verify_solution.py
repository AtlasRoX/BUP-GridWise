"""
GridWise Solution Verification Script (Independent Judge Replica)
Performs deep semantic and physical validation on all 10 public sample cases:
1. HTTP status & response schema
2. Note index alignment & directive semantics (type, hours, factors, reserve, caps)
3. Independent per-hour physical energy balance
4. Effective solar bounds & directive enforcement
5. Battery state continuity, capacity, reserve floor, and rate limits
6. Terminal battery neutrality
7. Aggregates recomputation (total grid, total cost, peak grid)
8. Official quality ratio & optimization score

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


def verify_case_directives(actual_directives, expected_directives, tolerance=0.01):
    """Deep verification of directive interpretation against ground truth."""
    if len(actual_directives) != len(expected_directives):
        return False, f"Directive count mismatch: expected {len(expected_directives)}, got {len(actual_directives)}"

    for i, exp in enumerate(expected_directives):
        act = actual_directives[i]
        if act.get("note_index") != exp.get("note_index", i):
            return False, f"Note index mismatch at position {i}: expected {exp.get('note_index')}, got {act.get('note_index')}"
        if act.get("applies") != exp.get("applies"):
            return False, f"Applies mismatch at note {i}: expected {exp.get('applies')}, got {act.get('applies')}"
        if act.get("directive_type") != exp.get("directive_type"):
            return False, f"Type mismatch at note {i}: expected {exp.get('directive_type')}, got {act.get('directive_type')}"

        # Check adjustments if applies is True
        if exp.get("applies"):
            exp_adj = exp.get("structured_adjustment") or {}
            act_adj = act.get("structured_adjustment") or {}

            # Hours check
            if "hours" in exp_adj:
                if sorted(act_adj.get("hours", [])) != sorted(exp_adj.get("hours", [])):
                    return False, f"Hours mismatch at note {i}: expected {exp_adj.get('hours')}, got {act_adj.get('hours')}"

            # Solar factor
            if "factor" in exp_adj:
                if abs(act_adj.get("factor", 0.0) - exp_adj.get("factor", 0.0)) > tolerance:
                    return False, f"Factor mismatch at note {i}: expected {exp_adj.get('factor')}, got {act_adj.get('factor')}"

            # Minimum energy
            if "minimum_energy_kwh" in exp_adj:
                if abs(act_adj.get("minimum_energy_kwh", 0.0) - exp_adj.get("minimum_energy_kwh", 0.0)) > tolerance:
                    return False, f"Reserve mismatch at note {i}: expected {exp_adj.get('minimum_energy_kwh')}, got {act_adj.get('minimum_energy_kwh')}"

            # Max grid
            if "max_grid_kwh" in exp_adj:
                if abs(act_adj.get("max_grid_kwh", 0.0) - exp_adj.get("max_grid_kwh", 0.0)) > tolerance:
                    return False, f"Max grid mismatch at note {i}: expected {exp_adj.get('max_grid_kwh')}, got {act_adj.get('max_grid_kwh')}"
        else:
            if act.get("structured_adjustment") is not None:
                return False, f"Structured adjustment must be null when applies is False at note {i}"

    return True, "Directives verified"


def replay_verify_physics(case_input, response_data, tolerance=0.01):
    """Completely independent 24-hour physical validation of final schedule."""
    hours_input = case_input["hours"]
    battery = case_input["battery"]
    plan = response_data.get("hourly_plan", [])
    directives = response_data.get("directive_interpretation", [])

    if len(plan) != 24:
        return False, f"Plan must have 24 hours, got {len(plan)}"

    # Independent reference constraint calculation directly from directives
    effective_solar = [h["solar_kwh"] for h in hours_input]
    reserve_floor = [battery["minimum_energy_kwh"] for _ in range(24)]
    charge_allowed = [True for _ in range(24)]
    discharge_allowed = [True for _ in range(24)]
    grid_cap = [float("inf") for _ in range(24)]

    for d in directives:
        if not d.get("applies"):
            continue
        dtype = d.get("directive_type")
        adj = d.get("structured_adjustment") or {}
        aff_hours = adj.get("hours", [])
        if dtype == "solar_reduction":
            for h in aff_hours:
                effective_solar[h] *= adj.get("factor", 1.0)
        elif dtype == "minimum_battery_reserve":
            for h in aff_hours:
                reserve_floor[h] = max(reserve_floor[h], adj.get("minimum_energy_kwh", 0.0))
        elif dtype == "no_charge_window":
            for h in aff_hours:
                charge_allowed[h] = False
        elif dtype == "no_discharge_window":
            for h in aff_hours:
                discharge_allowed[h] = False
        elif dtype == "max_grid_window":
            for h in aff_hours:
                grid_cap[h] = min(grid_cap[h], adj.get("max_grid_kwh", float("inf")))

    # Verify each hour
    prev_energy = battery["initial_energy_kwh"]
    calc_grid = 0.0
    calc_cost = 0.0
    calc_peak = 0.0

    for h in range(24):
        entry = plan[h]
        if entry.get("hour") != h:
            return False, f"Hour ordering error: expected {h}, got {entry.get('hour')}"

        grid = entry.get("grid_kwh", 0.0)
        solar = entry.get("solar_used_kwh", 0.0)
        action = entry.get("battery_action", "idle")
        bat_kwh = entry.get("battery_kwh", 0.0)
        energy_after = entry.get("battery_energy_after_kwh", 0.0)
        demand = hours_input[h]["demand_kwh"]
        tariff = hours_input[h]["tariff_bdt_per_kwh"]

        if grid < -tolerance:
            return False, f"Hour {h}: negative grid import ({grid})"
        if solar < -tolerance:
            return False, f"Hour {h}: negative solar usage ({solar})"
        if bat_kwh < -tolerance:
            return False, f"Hour {h}: negative battery energy ({bat_kwh})"

        # Solar bound
        if solar > effective_solar[h] + tolerance:
            return False, f"Hour {h}: solar used {solar:.2f} exceeds effective {effective_solar[h]:.2f}"

        # Grid cap
        if grid > grid_cap[h] + tolerance:
            return False, f"Hour {h}: grid {grid:.2f} exceeds cap {grid_cap[h]:.2f}"

        # Battery action
        charge_kwh = bat_kwh if action == "charge" else 0.0
        discharge_kwh = bat_kwh if action == "discharge" else 0.0

        if action == "charge":
            if not charge_allowed[h]:
                return False, f"Hour {h}: charging prohibited by directive"
            if bat_kwh > battery["max_charge_kwh_per_hour"] + tolerance:
                return False, f"Hour {h}: charge exceeds max rate"
        elif action == "discharge":
            if not discharge_allowed[h]:
                return False, f"Hour {h}: discharging prohibited by directive"
            if bat_kwh > battery["max_discharge_kwh_per_hour"] + tolerance:
                return False, f"Hour {h}: discharge exceeds max rate"
        elif action == "idle":
            if bat_kwh > tolerance:
                return False, f"Hour {h}: idle action but battery_kwh > 0"
        else:
            return False, f"Hour {h}: invalid battery action '{action}'"

        # Energy balance
        balance = (grid + solar + discharge_kwh) - (demand + charge_kwh)
        if abs(balance) > tolerance:
            return False, f"Hour {h}: energy balance violated (discrepancy {balance:.4f})"

        # State transition
        expected_energy = prev_energy + charge_kwh - discharge_kwh
        if abs(energy_after - expected_energy) > tolerance:
            return False, f"Hour {h}: battery transition error (expected {expected_energy:.2f}, got {energy_after:.2f})"

        # Reserve and Capacity bounds
        if energy_after < reserve_floor[h] - tolerance:
            return False, f"Hour {h}: energy {energy_after:.2f} below reserve floor {reserve_floor[h]:.2f}"
        if energy_after > battery["capacity_kwh"] + tolerance:
            return False, f"Hour {h}: energy {energy_after:.2f} exceeds capacity {battery['capacity_kwh']:.2f}"

        prev_energy = energy_after
        calc_grid += grid
        calc_cost += grid * tariff
        calc_peak = max(calc_peak, grid)

    # Neutrality check
    if abs(prev_energy - battery["initial_energy_kwh"]) > tolerance:
        return False, f"End-of-day battery neutrality violated: started {battery['initial_energy_kwh']}, ended {prev_energy:.2f}"

    # Aggregates check
    resp_grid = response_data.get("total_grid_kwh", 0.0)
    resp_cost = response_data.get("total_cost_bdt", 0.0)
    resp_peak = response_data.get("peak_grid_kwh", 0.0)

    if abs(calc_grid - resp_grid) > tolerance:
        return False, f"Total grid mismatch: recomputed {calc_grid:.2f} vs response {resp_grid:.2f}"
    if abs(calc_cost - resp_cost) > tolerance:
        return False, f"Total cost mismatch: recomputed {calc_cost:.2f} vs response {resp_cost:.2f}"
    if abs(calc_peak - resp_peak) > tolerance:
        return False, f"Peak grid mismatch: recomputed {calc_peak:.2f} vs response {resp_peak:.2f}"

    return True, "Physical constraints and aggregates verified"


async def run_verification(base_url: str = None):
    cases = load_sample_cases()
    print("=" * 90)
    print(f"GRIDWISE MASTER SOLUTION VERIFIER (Full Organizer Judge Replica)")
    print(f"Evaluating {len(cases)} Public Reference Cases")
    if base_url:
        print(f"Target: Live HTTP Service at {base_url}")
        client = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(60.0, connect=60.0))
    else:
        print("Target: In-Process ASGI Test Client")
        transport = httpx.ASGITransport(app=app)
        client = httpx.AsyncClient(transport=transport, base_url="http://test", timeout=45.0)

    try:
        # Check /health
        health_resp = await client.get("/health")
        if health_resp.status_code != 200 or health_resp.json().get("status") != "ok":
            print(f"[-] /health check failed! Status: {health_resp.status_code}, Body: {health_resp.text}")
            return False
        print("[+] /health check PASSED (status: ok)\n")

        print(f"{'Case ID':<10} {'Directives':<12} {'Physics':<10} {'Team Cost':<14} {'Ref Cost':<14} {'Quality':<10} {'Status':<10}")
        print("-" * 90)

        all_passed = True
        quality_ratios = []

        for case in cases:
            case_id = case["id"]
            case_input = case["input"]
            expected = case["expected_output"]
            ref_cost = expected["total_cost_bdt"]

            resp = await client.post("/optimize-energy", json=case_input)
            if resp.status_code != 200:
                print(f"{case_id:<10} {'FAIL':<12} {'FAIL':<10} {'HTTP ' + str(resp.status_code):<14} {ref_cost:<14.2f} {'0.0000':<10} {'FAIL':<10}")
                all_passed = False
                continue

            data = resp.json()
            team_cost = data.get("total_cost_bdt", 0.0)
            q_ratio = min(1.0, ref_cost / team_cost) if team_cost > 0 else 0.0
            quality_ratios.append(q_ratio)

            # 1. Deep Directive Verification
            dir_ok, dir_msg = verify_case_directives(data.get("directive_interpretation", []), expected.get("directive_interpretation", []))

            # 2. Deep Physical Replay & Aggregates Verification
            phy_ok, phy_msg = replay_verify_physics(case_input, data)

            passed = dir_ok and phy_ok and (q_ratio >= 0.99)
            if not passed:
                all_passed = False

            dir_str = "PASS" if dir_ok else "FAIL"
            phy_str = "PASS" if phy_ok else "FAIL"
            status_str = "PASS" if passed else "FAIL"

            print(f"{case_id:<10} {dir_str:<12} {phy_str:<10} {team_cost:<14.2f} {ref_cost:<14.2f} {q_ratio:<10.4f} {status_str:<10}")
            if not dir_ok:
                print(f"   [!] Directive error: {dir_msg}")
            if not phy_ok:
                print(f"   [!] Physics error: {phy_msg}")

        print("-" * 90)
        avg_quality = sum(quality_ratios) / len(quality_ratios) if quality_ratios else 0.0
        opt_score = avg_quality * 10.0
        print(f"Average Quality Ratio: {avg_quality:.4f} | Optimization Score: {opt_score:.2f} / 10.00")
        print(f"Overall Result: {'ALL CHECKS PASSED - JUDGE READY' if all_passed else 'SOME CHECKS FAILED'}")
        print("=" * 90)
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
