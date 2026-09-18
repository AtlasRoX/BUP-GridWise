from typing import List, Any
from app.models.directives import (
    DirectiveInterpretation,
    SolarReductionAdjustment,
    BatteryReserveAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
)
from app.models.request import ScenarioRequest
from app.models.response import HourlyPlanEntry


class ReplayValidationError(Exception):
    pass


def replay_validate_plan(
    plan: List[HourlyPlanEntry],
    scenario: ScenarioRequest,
    directives: List[DirectiveInterpretation],
    total_grid_kwh: float,
    total_cost_bdt: float,
    peak_grid_kwh: float,
    tolerance: float = 0.01,
) -> None:
    """
    Independent hour-by-hour re-evaluation of the entire schedule against
    energy balance, effective solar, battery dynamics, rate limits, directive rules,
    end-of-day neutrality, and aggregate totals.
    This evaluator calculates reference constraints strictly independently
    from raw validated directives without consuming production compiler output.
    """
    if len(plan) != 24:
        raise ReplayValidationError(f"Hourly plan must have 24 hours, got {len(plan)}")

    effective_solar = [h.solar_kwh for h in scenario.hours]
    reserve_floor = [scenario.battery.minimum_energy_kwh for _ in range(24)]
    charge_allowed = [True for _ in range(24)]
    discharge_allowed = [True for _ in range(24)]
    grid_cap = [float("inf") for _ in range(24)]

    for d in (directives or []):
        if not getattr(d, "applies", False) or getattr(d, "structured_adjustment", None) is None:
            continue
        adj = d.structured_adjustment
        if d.directive_type == "solar_reduction" and isinstance(adj, SolarReductionAdjustment):
            for h in adj.hours:
                effective_solar[h] *= adj.factor
        elif d.directive_type == "minimum_battery_reserve" and isinstance(adj, BatteryReserveAdjustment):
            for h in adj.hours:
                reserve_floor[h] = max(reserve_floor[h], adj.minimum_energy_kwh)
        elif d.directive_type == "no_charge_window" and isinstance(adj, HoursOnlyAdjustment):
            for h in adj.hours:
                charge_allowed[h] = False
        elif d.directive_type == "no_discharge_window" and isinstance(adj, HoursOnlyAdjustment):
            for h in adj.hours:
                discharge_allowed[h] = False
        elif d.directive_type == "max_grid_window" and isinstance(adj, MaxGridAdjustment):
            for h in adj.hours:
                grid_cap[h] = min(grid_cap[h], adj.max_grid_kwh)

    battery = scenario.battery
    prev_energy = battery.initial_energy_kwh

    sum_grid = 0.0
    sum_cost = 0.0
    max_grid = 0.0

    for h in range(24):
        entry = plan[h]
        hour_data = scenario.hours[h]

        if entry.hour != h:
            raise ReplayValidationError(f"Hour mismatch: expected {h}, got {entry.hour}")

        # 1. Non-negativity
        if entry.grid_kwh < -tolerance:
            raise ReplayValidationError(f"Hour {h}: negative grid import {entry.grid_kwh}")
        if entry.solar_used_kwh < -tolerance:
            raise ReplayValidationError(f"Hour {h}: negative solar used {entry.solar_used_kwh}")
        if entry.battery_kwh < -tolerance:
            raise ReplayValidationError(f"Hour {h}: negative battery energy {entry.battery_kwh}")

        # 2. Solar bound
        if entry.solar_used_kwh > effective_solar[h] + tolerance:
            raise ReplayValidationError(
                f"Hour {h}: solar used ({entry.solar_used_kwh}) exceeds effective solar ({effective_solar[h]})"
            )

        # 3. Grid cap bound
        if entry.grid_kwh > grid_cap[h] + tolerance:
            raise ReplayValidationError(
                f"Hour {h}: grid import ({entry.grid_kwh}) exceeds grid cap ({grid_cap[h]})"
            )

        # 4. Battery actions & rates
        charge_kwh = 0.0
        discharge_kwh = 0.0

        if entry.battery_action == "idle":
            if entry.battery_kwh > tolerance:
                raise ReplayValidationError(
                    f"Hour {h}: idle battery action has non-zero battery_kwh ({entry.battery_kwh})"
                )
        elif entry.battery_action == "charge":
            charge_kwh = entry.battery_kwh
            if not charge_allowed[h] and charge_kwh > tolerance:
                raise ReplayValidationError(f"Hour {h}: battery charged during prohibited window")
            if charge_kwh > battery.max_charge_kwh_per_hour + tolerance:
                raise ReplayValidationError(
                    f"Hour {h}: charge ({charge_kwh}) exceeds max_charge ({battery.max_charge_kwh_per_hour})"
                )
        elif entry.battery_action == "discharge":
            discharge_kwh = entry.battery_kwh
            if not discharge_allowed[h] and discharge_kwh > tolerance:
                raise ReplayValidationError(f"Hour {h}: battery discharged during prohibited window")
            if discharge_kwh > battery.max_discharge_kwh_per_hour + tolerance:
                raise ReplayValidationError(
                    f"Hour {h}: discharge ({discharge_kwh}) exceeds max_discharge ({battery.max_discharge_kwh_per_hour})"
                )
        else:
            raise ReplayValidationError(f"Hour {h}: invalid battery action {entry.battery_action}")

        # 5. Energy balance: grid + solar_used + discharge = demand + charge
        supply = entry.grid_kwh + entry.solar_used_kwh + discharge_kwh
        consumption = hour_data.demand_kwh + charge_kwh
        if abs(supply - consumption) > tolerance:
            raise ReplayValidationError(
                f"Hour {h}: energy balance violated (supply={supply}, consumption={consumption}, diff={abs(supply - consumption)})"
            )

        # 6. Battery state transition: E[h] = E[h-1] + charge - discharge
        expected_energy = prev_energy + charge_kwh - discharge_kwh
        if abs(entry.battery_energy_after_kwh - expected_energy) > tolerance:
            raise ReplayValidationError(
                f"Hour {h}: battery state mismatch (reported={entry.battery_energy_after_kwh}, calculated={expected_energy})"
            )

        # 7. Battery capacity & reserve floor bounds
        reserve_floor_h = reserve_floor[h]
        if entry.battery_energy_after_kwh < reserve_floor_h - tolerance:
            raise ReplayValidationError(
                f"Hour {h}: battery energy ({entry.battery_energy_after_kwh}) below reserve floor ({reserve_floor_h})"
            )
        if entry.battery_energy_after_kwh > battery.capacity_kwh + tolerance:
            raise ReplayValidationError(
                f"Hour {h}: battery energy ({entry.battery_energy_after_kwh}) exceeds capacity ({battery.capacity_kwh})"
            )

        prev_energy = entry.battery_energy_after_kwh

        sum_grid += entry.grid_kwh
        sum_cost += entry.grid_kwh * hour_data.tariff_bdt_per_kwh
        max_grid = max(max_grid, entry.grid_kwh)

    # 8. End-of-day battery neutrality: E[23] == E[initial]
    if abs(prev_energy - battery.initial_energy_kwh) > tolerance:
        raise ReplayValidationError(
            f"End-of-day battery neutrality violated: final={prev_energy}, initial={battery.initial_energy_kwh}"
        )

    # 9. Aggregate consistency checks
    if abs(total_grid_kwh - sum_grid) > tolerance:
        raise ReplayValidationError(
            f"Total grid kWh mismatch: reported={total_grid_kwh}, calculated={sum_grid}"
        )
    if abs(total_cost_bdt - sum_cost) > tolerance:
        raise ReplayValidationError(
            f"Total cost BDT mismatch: reported={total_cost_bdt}, calculated={sum_cost}"
        )
    if abs(peak_grid_kwh - max_grid) > tolerance:
        raise ReplayValidationError(
            f"Peak grid kWh mismatch: reported={peak_grid_kwh}, calculated={max_grid}"
        )
