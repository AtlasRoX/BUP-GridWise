import math
from typing import List
from pydantic import BaseModel, Field, field_validator, model_validator


class HourEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23, description="Hour of the day (0-23)")
    demand_kwh: float = Field(..., ge=0.0, description="Forecasted electrical demand in kWh")
    solar_kwh: float = Field(..., ge=0.0, description="Forecasted rooftop solar generation in kWh")
    tariff_bdt_per_kwh: float = Field(..., ge=0.0, description="Grid tariff rate in BDT per kWh")

    @field_validator("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh")
    @classmethod
    def check_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Numeric values must be finite")
        return v


class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(..., gt=0.0, description="Total battery storage capacity in kWh")
    initial_energy_kwh: float = Field(..., ge=0.0, description="Energy stored at start of day (hour 0) in kWh")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Minimum operational reserve floor in kWh")
    max_charge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum charge rate in kWh/hour")
    max_discharge_kwh_per_hour: float = Field(..., ge=0.0, description="Maximum discharge rate in kWh/hour")

    @field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
    )
    @classmethod
    def check_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("Battery parameters must be finite")
        return v

    @model_validator(mode="after")
    def check_battery_coherence(self) -> "BatteryConfig":
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be less than minimum_energy_kwh")
        return self


class ScenarioRequest(BaseModel):
    scenario_id: str = Field(..., min_length=1, description="Unique scenario identifier")
    operator_notes: List[str] = Field(..., min_length=1, max_length=3, description="1-3 natural-language operator directives")
    hours: List[HourEntry] = Field(..., min_length=24, max_length=24, description="Exactly 24 hourly profiles")
    battery: BatteryConfig = Field(..., description="Battery specifications")

    @field_validator("operator_notes")
    @classmethod
    def check_notes(cls, notes: List[str]) -> List[str]:
        if not (1 <= len(notes) <= 3):
            raise ValueError("operator_notes must contain between 1 and 3 items")
        for i, note in enumerate(notes):
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"operator_notes[{i}] cannot be empty or whitespace-only")
        return notes

    @field_validator("hours")
    @classmethod
    def check_hours_sequence(cls, hours: List[HourEntry]) -> List[HourEntry]:
        if len(hours) != 24:
            raise ValueError("hours must contain exactly 24 entries")
        hours_indices = [h.hour for h in hours]
        if hours_indices != list(range(24)):
            raise ValueError("hours must be strictly indexed from 0 to 23 without gaps or duplicates")
        return hours
