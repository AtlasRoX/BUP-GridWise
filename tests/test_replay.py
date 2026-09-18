import pytest
from app.models.request import ScenarioRequest, HourEntry, BatteryConfig
from app.models.response import HourlyPlanEntry
from app.rules.directive_compiler import compile_directives
from app.validation.replay import replay_validate_plan, ReplayValidationError


@pytest.fixture
def baseline_scenario():
    hours = [
        HourEntry(hour=h, demand_kwh=100.0, solar_kwh=20.0, tariff_bdt_per_kwh=10.0)
        for h in range(24)
    ]
    battery = BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )
    return ScenarioRequest(
        scenario_id="test_scenario",
        operator_notes=["Routine operations."],
        hours=hours,
        battery=battery,
    )


def test_replay_catches_energy_balance_violation(baseline_scenario):
    compiled = compile_directives(baseline_scenario.hours, baseline_scenario.battery, [])
    # Plan with artificial imbalance at hour 0
    plan = [
        HourlyPlanEntry(
            hour=h,
            grid_kwh=80.0 if h != 0 else 70.0,  # 10 kWh missing supply at hour 0!
            solar_used_kwh=20.0,
            battery_action="idle",
            battery_kwh=0.0,
            battery_energy_after_kwh=100.0,
        )
        for h in range(24)
    ]

    total_grid = sum(e.grid_kwh for e in plan)
    total_cost = sum(e.grid_kwh * 10.0 for e in plan)
    peak_grid = 80.0

    with pytest.raises(ReplayValidationError, match="energy balance violated"):
        replay_validate_plan(plan, baseline_scenario, [], total_grid, total_cost, peak_grid)


def test_replay_catches_neutrality_violation(baseline_scenario):
    # Plan where battery is discharged at hour 23 but doesn't restore to initial
    plan = [
        HourlyPlanEntry(
            hour=h,
            grid_kwh=80.0,
            solar_used_kwh=20.0,
            battery_action="idle",
            battery_kwh=0.0,
            battery_energy_after_kwh=100.0 if h < 23 else 80.0,
        )
        for h in range(24)
    ]

    total_grid = sum(e.grid_kwh for e in plan)
    total_cost = sum(e.grid_kwh * 10.0 for e in plan)
    peak_grid = 80.0

    with pytest.raises(ReplayValidationError):
        replay_validate_plan(plan, baseline_scenario, [], total_grid, total_cost, peak_grid)
