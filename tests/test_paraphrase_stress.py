import pytest
from app.models.request import BatteryConfig
from tests.fixtures.fake_interpreter import fallback_interpret_note


@pytest.fixture
def test_battery():
    return BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=30.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )


def test_paraphrase_solar_reduction(test_battery):
    note = "Solar output drops to one-fifth between 1 PM and 3 PM due to haze."
    interp = fallback_interpret_note(0, note, test_battery)
    assert interp.applies is True
    assert interp.directive_type == "solar_reduction"
    assert interp.structured_adjustment.hours == [13, 14]
    assert abs(interp.structured_adjustment.factor - 0.2) < 1e-4


def test_paraphrase_percentage_reserve(test_battery):
    note = "Keep at least 50% of the battery capacity stored from 6 PM until 9 PM for storm readiness."
    interp = fallback_interpret_note(0, note, test_battery)
    assert interp.applies is True
    assert interp.directive_type == "minimum_battery_reserve"
    assert interp.structured_adjustment.hours == [18, 19, 20]
    assert abs(interp.structured_adjustment.minimum_energy_kwh - 100.0) < 1e-4


def test_paraphrase_charge_disabled(test_battery):
    note = "The charger circuit is disabled from 2 AM until 5 AM."
    interp = fallback_interpret_note(0, note, test_battery)
    assert interp.applies is True
    assert interp.directive_type == "no_charge_window"
    assert interp.structured_adjustment.hours == [2, 3, 4]


def test_paraphrase_discharge_prohibited(test_battery):
    note = "Do not discharge the battery from 6 PM until 8 PM."
    interp = fallback_interpret_note(0, note, test_battery)
    assert interp.applies is True
    assert interp.directive_type == "no_discharge_window"
    assert interp.structured_adjustment.hours == [18, 19]


def test_paraphrase_grid_cap(test_battery):
    note = "Grid import must not exceed 155 kWh from 6 PM until 9 PM."
    interp = fallback_interpret_note(0, note, test_battery)
    assert interp.applies is True
    assert interp.directive_type == "max_grid_window"
    assert interp.structured_adjustment.hours == [18, 19, 20]
    assert abs(interp.structured_adjustment.max_grid_kwh - 155.0) < 1e-4


def test_paraphrase_distractor(test_battery):
    note = "The cafeteria added new breakfast combos for exam week."
    interp = fallback_interpret_note(0, note, test_battery)
    assert interp.applies is False
    assert interp.directive_type == "no_op"
    assert interp.structured_adjustment is None
