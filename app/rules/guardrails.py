from typing import List
from app.models.directives import (
    DirectiveInterpretation,
    SolarReductionAdjustment,
    BatteryReserveAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
)
from app.models.request import BatteryConfig


class GuardrailValidationError(Exception):
    pass


def validate_directives(
    directives: List[DirectiveInterpretation],
    num_notes: int,
    battery: BatteryConfig,
) -> List[DirectiveInterpretation]:
    """
    Strict deterministic validation firewall for directive interpretations.
    Enforces exact contract rules before any directive enters the optimizer.
    """
    if len(directives) != num_notes:
        raise GuardrailValidationError(
            f"Expected {num_notes} directive interpretations, got {len(directives)}"
        )

    seen_indices = set()
    validated_list: List[DirectiveInterpretation] = []

    for expected_idx, directive in enumerate(directives):
        if directive.note_index != expected_idx:
            raise GuardrailValidationError(
                f"Directive note_index mismatch: expected {expected_idx}, got {directive.note_index}"
            )
        seen_indices.add(directive.note_index)

        # Validate applies and structured_adjustment consistency
        if directive.directive_type == "no_op":
            if directive.applies is not False:
                raise GuardrailValidationError(f"no_op directive at index {expected_idx} must have applies=false")
            if directive.structured_adjustment is not None:
                raise GuardrailValidationError(f"no_op directive at index {expected_idx} must have structured_adjustment=null")
        else:
            if directive.applies is not True:
                raise GuardrailValidationError(f"Directive {directive.directive_type} at index {expected_idx} must have applies=true")
            if directive.structured_adjustment is None:
                raise GuardrailValidationError(f"Directive {directive.directive_type} at index {expected_idx} missing structured_adjustment")

            adj = directive.structured_adjustment
            hours = adj.hours
            if not hours:
                raise GuardrailValidationError(f"Directive at index {expected_idx} has empty hours list")
            if hours != sorted(list(set(hours))):
                raise GuardrailValidationError(f"Directive at index {expected_idx} hours must be unique and sorted: {hours}")
            for h in hours:
                if not (0 <= h <= 23):
                    raise GuardrailValidationError(f"Hour {h} out of range [0, 23] at index {expected_idx}")

            # Specific adjustment checks
            if isinstance(adj, SolarReductionAdjustment):
                if not (0.0 <= adj.factor <= 1.0):
                    raise GuardrailValidationError(f"Solar factor {adj.factor} out of range [0.0, 1.0]")
            elif isinstance(adj, BatteryReserveAdjustment):
                if adj.minimum_energy_kwh < 0.0:
                    raise GuardrailValidationError(f"Battery reserve {adj.minimum_energy_kwh} cannot be negative")
                if adj.minimum_energy_kwh > battery.capacity_kwh:
                    raise GuardrailValidationError(
                        f"Battery reserve {adj.minimum_energy_kwh} exceeds battery capacity {battery.capacity_kwh}"
                    )
            elif isinstance(adj, MaxGridAdjustment):
                if adj.max_grid_kwh < 0.0:
                    raise GuardrailValidationError(f"Max grid cap {adj.max_grid_kwh} cannot be negative")

        validated_list.append(directive)

    return validated_list
