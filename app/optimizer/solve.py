import logging
from typing import List
import numpy as np
from scipy.optimize import milp
from app.config import settings
from app.models.request import HourEntry, BatteryConfig
from app.models.response import HourlyPlanEntry, BatteryAction
from app.optimizer.model import (
    build_milp_problem,
    IDX_G,
    IDX_S,
    IDX_C,
    IDX_D,
    IDX_E,
    NUM_HOURS,
)
from app.rules.directive_compiler import CompiledConstraints

logger = logging.getLogger("gridwise.optimizer.solve")


class OptimizationInfeasibleError(Exception):
    pass


def clean_num(val: float, precision: int = 4) -> float:
    """Rounds float and converts close-to-integer values."""
    r = round(val, precision)
    if abs(r - round(r)) < 1e-5:
        return float(int(round(r)))
    return r


def solve_schedule(
    hours: List[HourEntry],
    battery: BatteryConfig,
    compiled: CompiledConstraints,
) -> List[HourlyPlanEntry]:
    """
    Constructs and solves the 24-hour MILP problem using SciPy HiGHS.
    Reconstructs the HourlyPlanEntry array.
    """
    problem = build_milp_problem(hours, battery, compiled)

    options = {
        "time_limit": settings.solver_timeout_seconds,
        "disp": False,
        "mip_rel_gap": 1e-6,
    }

    res = milp(
        c=problem.c,
        integrality=problem.integrality,
        bounds=problem.bounds,
        constraints=problem.constraints,
        options=options,
    )

    if not res.success or res.x is None:
        logger.error(f"MILP solve failed: status={res.status}, message={res.message}")
        raise OptimizationInfeasibleError(f"Optimization failed to find a feasible solution: {res.message}")

    x = res.x
    plan: List[HourlyPlanEntry] = []

    EPS = 1e-5

    for h in range(NUM_HOURS):
        grid_kwh = max(0.0, float(x[IDX_G + h]))
        solar_used_kwh = max(0.0, float(x[IDX_S + h]))
        charge_kwh = max(0.0, float(x[IDX_C + h]))
        discharge_kwh = max(0.0, float(x[IDX_D + h]))
        energy_after_kwh = float(x[IDX_E + h])

        # Action classification
        if charge_kwh > EPS:
            action: BatteryAction = "charge"
            battery_kwh = charge_kwh
        elif discharge_kwh > EPS:
            action: BatteryAction = "discharge"
            battery_kwh = discharge_kwh
        else:
            action: BatteryAction = "idle"
            battery_kwh = 0.0

        entry = HourlyPlanEntry(
            hour=h,
            grid_kwh=clean_num(grid_kwh),
            solar_used_kwh=clean_num(solar_used_kwh),
            battery_action=action,
            battery_kwh=clean_num(battery_kwh),
            battery_energy_after_kwh=clean_num(energy_after_kwh),
        )
        plan.append(entry)

    return plan
