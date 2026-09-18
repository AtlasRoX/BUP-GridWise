from typing import List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]


class SolarReductionAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Affected hour indices (0-23)")
    factor: float = Field(..., ge=0.0, le=1.0, description="Remaining usable fraction of solar output")

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("hours list cannot be empty")
        for h in v:
            if not (0 <= h <= 23):
                raise ValueError(f"Hour {h} out of bounds [0, 23]")
        if len(v) != len(set(v)):
            raise ValueError("hours must not contain duplicates")
        return sorted(v)


class BatteryReserveAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Affected hour indices (0-23)")
    minimum_energy_kwh: float = Field(..., ge=0.0, description="Target minimum battery energy in kWh")

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("hours list cannot be empty")
        for h in v:
            if not (0 <= h <= 23):
                raise ValueError(f"Hour {h} out of bounds [0, 23]")
        if len(v) != len(set(v)):
            raise ValueError("hours must not contain duplicates")
        return sorted(v)


class HoursOnlyAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Affected hour indices (0-23)")

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("hours list cannot be empty")
        for h in v:
            if not (0 <= h <= 23):
                raise ValueError(f"Hour {h} out of bounds [0, 23]")
        if len(v) != len(set(v)):
            raise ValueError("hours must not contain duplicates")
        return sorted(v)


class MaxGridAdjustment(BaseModel):
    hours: List[int] = Field(..., description="Affected hour indices (0-23)")
    max_grid_kwh: float = Field(..., ge=0.0, description="Maximum permitted grid import in kWh")

    @field_validator("hours")
    @classmethod
    def validate_hours(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("hours list cannot be empty")
        for h in v:
            if not (0 <= h <= 23):
                raise ValueError(f"Hour {h} out of bounds [0, 23]")
        if len(v) != len(set(v)):
            raise ValueError("hours must not contain duplicates")
        return sorted(v)


StructuredAdjustment = Union[
    SolarReductionAdjustment,
    BatteryReserveAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
]


class DirectiveInterpretation(BaseModel):
    note_index: int = Field(..., ge=0, description="Index of the operator note (0-based)")
    applies: bool = Field(..., description="True if note imposes a valid scheduling directive; False for no_op")
    directive_type: DirectiveType = Field(..., description="Type of directive identified")
    structured_adjustment: Optional[StructuredAdjustment] = Field(
        default=None,
        description="Structured parameter adjustment object; null for no_op"
    )
    explanation: str = Field(
        default="Directive interpreted.",
        description="Concise explanation of the interpretation"
    )

    @model_validator(mode="after")
    def validate_consistency(self) -> "DirectiveInterpretation":
        if self.directive_type == "no_op":
            if self.applies is not False:
                raise ValueError("no_op directive must have applies=false")
            if self.structured_adjustment is not None:
                raise ValueError("no_op directive must have structured_adjustment=null")
        else:
            if self.applies is not True:
                raise ValueError(f"{self.directive_type} directive must have applies=true")
            if self.structured_adjustment is None:
                raise ValueError(f"{self.directive_type} directive must have non-null structured_adjustment")

            # Validate type match
            adj = self.structured_adjustment
            if self.directive_type == "solar_reduction" and not isinstance(adj, SolarReductionAdjustment):
                raise ValueError("solar_reduction requires SolarReductionAdjustment")
            elif self.directive_type == "minimum_battery_reserve" and not isinstance(adj, BatteryReserveAdjustment):
                raise ValueError("minimum_battery_reserve requires BatteryReserveAdjustment")
            elif self.directive_type in ("no_charge_window", "no_discharge_window") and not isinstance(adj, HoursOnlyAdjustment):
                raise ValueError(f"{self.directive_type} requires HoursOnlyAdjustment")
            elif self.directive_type == "max_grid_window" and not isinstance(adj, MaxGridAdjustment):
                raise ValueError("max_grid_window requires MaxGridAdjustment")

        return self
