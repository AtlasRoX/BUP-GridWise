from typing import List, Literal
from pydantic import BaseModel, Field
from app.models.directives import DirectiveInterpretation

BatteryAction = Literal["charge", "discharge", "idle"]


class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour index (0-23)")
    grid_kwh: float = Field(..., ge=0.0, description="Grid electricity imported in kWh")
    solar_used_kwh: float = Field(..., ge=0.0, description="Rooftop solar energy consumed in kWh")
    battery_action: BatteryAction = Field(..., description="Action taken: charge, discharge, or idle")
    battery_kwh: float = Field(..., ge=0.0, description="Energy charged or discharged during this hour in kWh (0 if idle)")
    battery_energy_after_kwh: float = Field(..., ge=0.0, description="Energy stored in battery at the end of this hour in kWh")


class HealthResponse(BaseModel):
    status: str = Field("ok", description="Service readiness status")


class OptimizeEnergyResponse(BaseModel):
    scenario_id: str = Field(..., description="Echo of input scenario_id")
    directive_interpretation: List[DirectiveInterpretation] = Field(
        ..., description="Interpreted directives matching operator_notes order"
    )
    hourly_plan: List[HourlyPlanEntry] = Field(
        ..., min_length=24, max_length=24, description="Optimal 24-hour dispatch schedule"
    )
    total_grid_kwh: float = Field(..., ge=0.0, description="Total grid energy imported across the 24-hour horizon")
    total_cost_bdt: float = Field(..., ge=0.0, description="Total electricity cost in BDT")
    peak_grid_kwh: float = Field(..., ge=0.0, description="Peak hourly grid import across the 24-hour horizon")
    plan_summary: str = Field(..., min_length=1, description="Concise summary of the optimization result")
