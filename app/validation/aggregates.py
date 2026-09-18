from typing import List, Tuple
from app.models.request import HourEntry
from app.models.response import HourlyPlanEntry


def compute_aggregates(
    plan: List[HourlyPlanEntry],
    hours: List[HourEntry],
) -> Tuple[float, float, float]:
    """
    Recomputes total_grid_kwh, total_cost_bdt, and peak_grid_kwh directly
    from the final hourly_plan object to guarantee 100% mathematical consistency.
    """
    total_grid_kwh = sum(entry.grid_kwh for entry in plan)
    total_cost_bdt = sum(
        entry.grid_kwh * hours[entry.hour].tariff_bdt_per_kwh for entry in plan
    )
    peak_grid_kwh = max(entry.grid_kwh for entry in plan)

    def clean(v: float) -> float:
        r = round(v, 4)
        if abs(r - round(r)) < 1e-5:
            return float(int(round(r)))
        return r

    return clean(total_grid_kwh), clean(total_cost_bdt), clean(peak_grid_kwh)
