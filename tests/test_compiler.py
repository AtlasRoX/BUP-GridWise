import pytest
from app.models.directives import (
    DirectiveInterpretation,
    SolarReductionAdjustment,
    BatteryReserveAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
)
from app.models.request import BatteryConfig, HourEntry
from app.rules.directive_compiler import compile_directives


@pytest.fixture
def sample_setup():
    hours = [
        HourEntry(hour=h, demand_kwh=100.0, solar_kwh=50.0, tariff_bdt_per_kwh=10.0)
        for h in range(24)
    ]
    battery = BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )
    return hours, battery


def test_compile_multi_directives(sample_setup):
    hours, battery = sample_setup
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type="solar_reduction",
            structured_adjustment=SolarReductionAdjustment(hours=[12, 13], factor=0.5),
            explanation="50% solar reduction",
        ),
        DirectiveInterpretation(
            note_index=1,
            applies=True,
            directive_type="minimum_battery_reserve",
            structured_adjustment=BatteryReserveAdjustment(hours=[18, 19, 20], minimum_energy_kwh=80.0),
            explanation="80 kWh reserve",
        ),
        DirectiveInterpretation(
            note_index=2,
            applies=True,
            directive_type="no_charge_window",
            structured_adjustment=HoursOnlyAdjustment(hours=[2, 3]),
            explanation="No charging 2-3 AM",
        ),
    ]

    compiled = compile_directives(hours, battery, directives)

    # Check solar reduction
    assert compiled.effective_solar[12] == 25.0
    assert compiled.effective_solar[13] == 25.0
    assert compiled.effective_solar[11] == 50.0

    # Check battery reserve floor
    assert compiled.reserve_floor[18] == 80.0
    assert compiled.reserve_floor[19] == 80.0
    assert compiled.reserve_floor[0] == 40.0  # baseline minimum

    # Check no-charge window
    assert compiled.charge_allowed[2] is False
    assert compiled.charge_allowed[3] is False
    assert compiled.charge_allowed[4] is True
