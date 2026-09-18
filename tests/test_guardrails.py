import pytest
from app.models.directives import (
    DirectiveInterpretation,
    SolarReductionAdjustment,
    BatteryReserveAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
)
from app.models.request import BatteryConfig
from app.rules.guardrails import validate_directives, GuardrailValidationError


@pytest.fixture
def sample_battery():
    return BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )


def test_valid_directives(sample_battery):
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type="solar_reduction",
            structured_adjustment=SolarReductionAdjustment(hours=[12, 13], factor=0.25),
            explanation="Solar cleaning",
        ),
        DirectiveInterpretation(
            note_index=1,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Irrelevant note",
        ),
    ]
    validated = validate_directives(directives, num_notes=2, battery=sample_battery)
    assert len(validated) == 2
    assert validated[0].applies is True
    assert validated[1].applies is False


def test_note_count_mismatch(sample_battery):
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Only 1 note",
        )
    ]
    with pytest.raises(GuardrailValidationError, match="Expected 2"):
        validate_directives(directives, num_notes=2, battery=sample_battery)


def test_note_index_out_of_order(sample_battery):
    directives = [
        DirectiveInterpretation(
            note_index=1,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Wrong order",
        ),
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Wrong order",
        ),
    ]
    with pytest.raises(GuardrailValidationError, match="note_index mismatch"):
        validate_directives(directives, num_notes=2, battery=sample_battery)


def test_reserve_exceeds_capacity(sample_battery):
    directives = [
        DirectiveInterpretation(
            note_index=0,
            applies=True,
            directive_type="minimum_battery_reserve",
            structured_adjustment=BatteryReserveAdjustment(hours=[18, 19], minimum_energy_kwh=250.0),
            explanation="Excessive reserve",
        )
    ]
    with pytest.raises(GuardrailValidationError, match="exceeds battery capacity"):
        validate_directives(directives, num_notes=1, battery=sample_battery)
