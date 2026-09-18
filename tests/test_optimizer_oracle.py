import itertools
import pytest
from app.models.request import HourEntry, BatteryConfig
from app.rules.directive_compiler import compile_directives
from app.optimizer.solve import solve_schedule
from app.validation.aggregates import compute_aggregates


def test_oracle_battery_arbitrage_optimality():
    """
    Brute-force discrete state oracle vs. HiGHS MILP solver.
    Scenario:
    24 hours with constant demand (50 kWh/h) and no solar.
    Base tariff: 10 BDT.
    Cheap hour (Hour 3): 2 BDT/kWh.
    Expensive hour (Hour 19): 20 BDT/kWh.
    Battery:
      capacity: 100 kWh, initial: 50 kWh, min_reserve: 20 kWh.
      max_charge: 30 kWh, max_discharge: 30 kWh.
      neutrality: E[23] == 50 kWh.
    """
    hours = [
        HourEntry(hour=h, demand_kwh=50.0, solar_kwh=0.0, tariff_bdt_per_kwh=10.0)
        for h in range(24)
    ]
    hours[3] = HourEntry(hour=3, demand_kwh=50.0, solar_kwh=0.0, tariff_bdt_per_kwh=2.0)
    hours[19] = HourEntry(hour=19, demand_kwh=50.0, solar_kwh=0.0, tariff_bdt_per_kwh=20.0)

    battery = BatteryConfig(
        capacity_kwh=100.0,
        initial_energy_kwh=50.0,
        minimum_energy_kwh=20.0,
        max_charge_kwh_per_hour=30.0,
        max_discharge_kwh_per_hour=30.0,
    )

    compiled = compile_directives(hours, battery, [])
    milp_plan = solve_schedule(hours, battery, compiled)
    _, milp_total_cost, _ = compute_aggregates(milp_plan, hours)

    # Brute force discrete search over possible charge amounts at Hour 3
    # and discharge amounts at Hour 19 in discrete increments of 1 kWh
    best_oracle_cost = float("inf")
    best_charge = 0.0
    best_discharge = 0.0

    # Base cost without any battery operation: 24 * 50 * tariff
    base_cost = sum(h.demand_kwh * h.tariff_bdt_per_kwh for h in hours)

    # In this convex setting with single cheap and single peak hour:
    # Any battery charge c at hr 3 increases hr 3 grid by c, cost by c * 2.0.
    # Discharging d at hr 19 reduces hr 19 grid by d, cost savings d * 20.0.
    # Terminal neutrality requires c == d.
    # Feasibility bounds:
    # 0 <= c <= max_charge (30)
    # initial + c <= capacity (50 + c <= 100 -> c <= 50)
    # 0 <= d <= max_discharge (30)
    # initial + c - d >= minimum (50 + c - d >= 20 -> 50 >= 20, holds for c == d)
    # Grid >= 0 at hr 19: demand - d >= 0 -> 50 - 30 = 20 >= 0.

    for charge_amt in range(0, 31, 1):
        discharge_amt = charge_amt  # Neutrality
        # Check energy bounds
        e_after_charge = 50.0 + charge_amt
        e_after_discharge = e_after_charge - discharge_amt
        if e_after_charge > 100.0 or e_after_discharge < 20.0:
            continue
        
        # Calculate cost
        cost = base_cost + (charge_amt * 2.0) - (discharge_amt * 20.0)
        if cost < best_oracle_cost:
            best_oracle_cost = cost
            best_charge = float(charge_amt)
            best_discharge = float(discharge_amt)

    # HiGHS MILP should discover the continuous optimal point
    # Since marginal return is positive (+18 BDT/kWh), max charge is 30 kWh
    assert abs(milp_total_cost - best_oracle_cost) < 1e-2
    assert abs(milp_plan[3].battery_kwh - best_charge) < 1e-2
    assert abs(milp_plan[19].battery_kwh - best_discharge) < 1e-2


def test_oracle_free_solar_prioritization():
    """
    Oracle check proving solver never imports grid when solar is freely available
    and never charges battery from grid when solar can cover it.
    """
    hours = [
        HourEntry(hour=h, demand_kwh=100.0, solar_kwh=100.0 if 10 <= h <= 14 else 0.0, tariff_bdt_per_kwh=12.0)
        for h in range(24)
    ]
    battery = BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )
    compiled = compile_directives(hours, battery, [])
    plan = solve_schedule(hours, battery, compiled)

    # In hours 10..14, solar exactly covers demand.
    # Grid should be 0.0 kWh and solar used should be exactly 100.0 kWh.
    for h in range(10, 15):
        assert abs(plan[h].solar_used_kwh - 100.0) < 1e-3
        assert abs(plan[h].grid_kwh - 0.0) < 1e-3


def test_oracle_grid_cap_forces_battery_discharge():
    """
    Oracle check: If grid is capped below demand and solar is 0,
    battery MUST discharge the remaining difference.
    Demand = 80, Grid cap = 50 -> Battery discharge MUST be 30.
    """
    hours = [
        HourEntry(hour=h, demand_kwh=50.0, solar_kwh=0.0, tariff_bdt_per_kwh=10.0)
        for h in range(24)
    ]
    hours[12] = HourEntry(hour=12, demand_kwh=80.0, solar_kwh=0.0, tariff_bdt_per_kwh=10.0)

    battery = BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )
    compiled = compile_directives(hours, battery, [])
    compiled.grid_cap[12] = 50.0  # Force cap

    plan = solve_schedule(hours, battery, compiled)

    # Grid at hour 12 cannot exceed 50.0
    assert plan[12].grid_kwh <= 50.0 + 1e-4
    # Battery discharge must provide at least the 30 kWh deficit to balance demand
    assert plan[12].battery_action == "discharge"
    assert plan[12].battery_kwh >= 30.0 - 1e-3
    assert abs(plan[12].grid_kwh + plan[12].battery_kwh - 80.0) < 1e-3
