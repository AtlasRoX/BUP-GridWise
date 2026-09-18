from dataclasses import dataclass
from typing import List
from app.models.directives import (
    DirectiveInterpretation,
    SolarReductionAdjustment,
    BatteryReserveAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
)
from app.models.request import HourEntry, BatteryConfig


@dataclass
class CompiledConstraints:
    effective_solar: List[float]
    reserve_floor: List[float]
    charge_allowed: List[bool]
    discharge_allowed: List[bool]
    grid_cap: List[float]


def compile_directives(
    hours: List[HourEntry],
    battery: BatteryConfig,
    directives: List[DirectiveInterpretation],
) -> CompiledConstraints:
    """
    Compiles all active validated directives into per-hour constraint arrays.
    Simultaneously handles multiple directives without overwriting.
    """
    effective_solar = [h.solar_kwh for h in hours]
    reserve_floor = [battery.minimum_energy_kwh for _ in range(24)]
    charge_allowed = [True for _ in range(24)]
    discharge_allowed = [True for _ in range(24)]
    grid_cap = [float("inf") for _ in range(24)]

    for directive in directives:
        if not directive.applies or directive.structured_adjustment is None:
            continue

        adj = directive.structured_adjustment

        if directive.directive_type == "solar_reduction" and isinstance(adj, SolarReductionAdjustment):
            for h in adj.hours:
                effective_solar[h] *= adj.factor

        elif directive.directive_type == "minimum_battery_reserve" and isinstance(adj, BatteryReserveAdjustment):
            for h in adj.hours:
                reserve_floor[h] = max(reserve_floor[h], adj.minimum_energy_kwh)

        elif directive.directive_type == "no_charge_window" and isinstance(adj, HoursOnlyAdjustment):
            for h in adj.hours:
                charge_allowed[h] = False

        elif directive.directive_type == "no_discharge_window" and isinstance(adj, HoursOnlyAdjustment):
            for h in adj.hours:
                discharge_allowed[h] = False

        elif directive.directive_type == "max_grid_window" and isinstance(adj, MaxGridAdjustment):
            for h in adj.hours:
                grid_cap[h] = min(grid_cap[h], adj.max_grid_kwh)

    return CompiledConstraints(
        effective_solar=effective_solar,
        reserve_floor=reserve_floor,
        charge_allowed=charge_allowed,
        discharge_allowed=discharge_allowed,
        grid_cap=grid_cap,
    )
