import pytest
from app.models.request import HourEntry, BatteryConfig
from app.optimizer.solve import solve_schedule
from app.rules.directive_compiler import compile_directives
from app.validation.aggregates import compute_aggregates
from app.validation.replay import replay_validate_plan
from app.models.directives import DirectiveInterpretation, HoursOnlyAdjustment, MaxGridAdjustment


def test_battery_arbitrage_and_neutrality():
    # Setup 24 hours: base tariff 10, cheap at hour 2 (tariff 2), expensive at hour 18 (tariff 25)
    hours = [
        HourEntry(hour=h, demand_kwh=100.0, solar_kwh=0.0, tariff_bdt_per_kwh=10.0)
        for h in range(24)
    ]
    hours[2] = HourEntry(hour=2, demand_kwh=100.0, solar_kwh=0.0, tariff_bdt_per_kwh=2.0)
    hours[18] = HourEntry(hour=18, demand_kwh=100.0, solar_kwh=0.0, tariff_bdt_per_kwh=25.0)

    battery = BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )

    compiled = compile_directives(hours, battery, [])
    plan = solve_schedule(hours, battery, compiled)

    # Battery should charge in cheap hour 2
    assert plan[2].battery_action == "charge"
    assert plan[2].battery_kwh > 0.0

    # Battery should discharge in expensive hour 18
    assert plan[18].battery_action == "discharge"
    assert plan[18].battery_kwh > 0.0

    # Final energy must equal initial energy (100.0)
    assert abs(plan[23].battery_energy_after_kwh - 100.0) < 1e-4

    # Aggregates and replay
    total_grid, total_cost, peak_grid = compute_aggregates(plan, hours)
    replay_validate_plan(
        plan=plan,
        scenario=type("MockScenario", (), {"hours": hours, "battery": battery})(),
        directives=[],
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
    )


def test_grid_cap_enforcement():
    hours = [
        HourEntry(hour=h, demand_kwh=120.0, solar_kwh=0.0, tariff_bdt_per_kwh=10.0)
        for h in range(24)
    ]
    battery = BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=20.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )

    cap_directive = DirectiveInterpretation(
        note_index=0,
        applies=True,
        directive_type="max_grid_window",
        structured_adjustment=MaxGridAdjustment(hours=[19, 20], max_grid_kwh=80.0),
        explanation="Grid cap 80 kWh at hours 19, 20",
    )

    compiled = compile_directives(hours, battery, [cap_directive])
    plan = solve_schedule(hours, battery, compiled)

    # In hours 19 and 20, grid import must be <= 80 kWh
    assert plan[19].grid_kwh <= 80.0 + 1e-5
    assert plan[20].grid_kwh <= 80.0 + 1e-5

    # Since demand is 120 and grid <= 80, battery must discharge at least 40 kWh
    assert plan[19].battery_action == "discharge"
    assert plan[19].battery_kwh >= 40.0 - 1e-5
