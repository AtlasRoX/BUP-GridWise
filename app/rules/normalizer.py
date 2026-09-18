import re
from typing import List, Optional, Tuple
from app.models.directives import (
    DirectiveType,
    SolarReductionAdjustment,
    BatteryReserveAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
    DirectiveInterpretation,
)
from app.models.request import BatteryConfig


def parse_time_window(text: str) -> Optional[List[int]]:
    """
    Extracts start-inclusive, end-exclusive hours from natural language.
    Examples:
      - 'from noon until 2 PM' -> [12, 13]
      - 'from 2 AM until 5 AM' -> [2, 3, 4]
      - 'from 6 PM until 9 PM' -> [18, 19, 20]
      - 'between 11 AM and 2 PM' -> [11, 12, 13]
    """
    text_lower = text.lower()

    # Normalize noon and midnight
    text_norm = text_lower.replace("noon", "12 pm").replace("midnight", "12 am")

    pattern = r"(?:from|between)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(?:until|to|and)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))"
    match = re.search(pattern, text_norm)
    if not match:
        return None

    start_str = match.group(1).strip()
    end_str = match.group(2).strip()

    def to_hour(s: str, default_meridiem: Optional[str] = None) -> Optional[int]:
        s = s.strip()
        has_am = "am" in s
        has_pm = "pm" in s
        digits = re.search(r"\d{1,2}", s)
        if not digits:
            return None
        val = int(digits.group(0))

        if not has_am and not has_pm and default_meridiem:
            has_am = "am" in default_meridiem
            has_pm = "pm" in default_meridiem

        if has_pm:
            return 12 if val == 12 else val + 12
        elif has_am:
            return 0 if val == 12 else val
        return val

    # End string always has meridiem in well-formed notes
    end_meridiem = "pm" if "pm" in end_str else ("am" if "am" in end_str else None)
    start_hour = to_hour(start_str, default_meridiem=end_meridiem)
    end_hour = to_hour(end_str)

    if start_hour is None or end_hour is None or start_hour >= end_hour:
        return None

    return list(range(start_hour, end_hour))


def parse_solar_factor(text: str) -> float:
    """
    Extracts remaining usable solar fraction from text.
    """
    text_lower = text.lower()
    if "one-fifth" in text_lower:
        return 0.2
    if "half" in text_lower or "50%" in text_lower:
        return 0.5
    if "25%" in text_lower or "one-quarter" in text_lower or "quarter" in text_lower:
        return 0.25

    pct_match = re.search(r"(\d{1,3})%\s*(?:reduction|drop|decrease)", text_lower)
    if pct_match:
        reduction_pct = float(pct_match.group(1))
        return max(0.0, min(1.0, 1.0 - (reduction_pct / 100.0)))

    pct_of_match = re.search(r"(\d{1,3})%\s*of\s*(?:the\s*)?forecast", text_lower)
    if pct_of_match:
        usable_pct = float(pct_of_match.group(1))
        return max(0.0, min(1.0, usable_pct / 100.0))

    return 0.25  # Safe default if undetermined


def parse_battery_reserve(text: str, battery: BatteryConfig) -> float:
    """
    Extracts battery reserve in kWh, handling relative percentage of capacity.
    """
    text_lower = text.lower()
    pct_match = re.search(r"(\d{1,3})%\s*of\s*(?:the\s*)?battery\s*capacity", text_lower)
    if pct_match:
        pct = float(pct_match.group(1))
        return battery.capacity_kwh * (pct / 100.0)

    kwh_match = re.search(r"(\d+(?:\.\d+)?)\s*kwh", text_lower)
    if kwh_match:
        return float(kwh_match.group(1))

    return battery.minimum_energy_kwh


def parse_grid_cap(text: str) -> float:
    """
    Extracts max grid import cap in kWh.
    """
    text_lower = text.lower()
    kwh_match = re.search(r"(?:exceed|limit|stay at or below|cap of)\s*(\d+(?:\.\d+)?)\s*kwh", text_lower)
    if kwh_match:
        return float(kwh_match.group(1))
    any_kwh = re.search(r"(\d+(?:\.\d+)?)\s*kwh", text_lower)
    if any_kwh:
        return float(any_kwh.group(1))
    return 150.0


def fallback_interpret_note(
    note_index: int, note_text: str, battery: BatteryConfig
) -> DirectiveInterpretation:
    """
    Deterministic fallback parser used if LLM provider is offline or unreachable.
    """
    text_lower = note_text.lower()
    hours = parse_time_window(note_text)

    # 1. Solar reduction
    if any(k in text_lower for k in ("solar", "rooftop", "photovoltaic", "pv", "cleaning", "cloud", "inverter")) and (
        any(k in text_lower for k in ("reduction", "forecast", "output", "wash", "half", "drops", "usable"))
    ):
        if hours:
            factor = parse_solar_factor(note_text)
            return DirectiveInterpretation(
                note_index=note_index,
                applies=True,
                directive_type="solar_reduction",
                structured_adjustment=SolarReductionAdjustment(hours=hours, factor=factor),
                explanation=f"Identified solar reduction in hours {hours} with factor {factor}.",
            )

    # 2. No charge window
    if any(k in text_lower for k in ("charger", "charging", "charge circuit", "charging circuit")) and any(
        k in text_lower for k in ("isolated", "maintenance", "unavailable", "disabled", "inspect")
    ):
        if hours:
            return DirectiveInterpretation(
                note_index=note_index,
                applies=True,
                directive_type="no_charge_window",
                structured_adjustment=HoursOnlyAdjustment(hours=hours),
                explanation=f"Charging disabled in hours {hours}.",
            )

    # 3. No discharge window
    if any(k in text_lower for k in ("not discharge", "do not discharge", "discharge prohibited", "relay testing", "protection testing")):
        if hours:
            return DirectiveInterpretation(
                note_index=note_index,
                applies=True,
                directive_type="no_discharge_window",
                structured_adjustment=HoursOnlyAdjustment(hours=hours),
                explanation=f"Discharging disabled in hours {hours}.",
            )

    # 4. Minimum battery reserve
    if any(k in text_lower for k in ("reserve", "emergency", "stored", "remain in the battery", "data center requires", "battery capacity")):
        if hours:
            reserve_kwh = parse_battery_reserve(note_text, battery)
            return DirectiveInterpretation(
                note_index=note_index,
                applies=True,
                directive_type="minimum_battery_reserve",
                structured_adjustment=BatteryReserveAdjustment(hours=hours, minimum_energy_kwh=reserve_kwh),
                explanation=f"Emergency battery reserve of {reserve_kwh} kWh required in hours {hours}.",
            )

    # 5. Max grid window
    if any(k in text_lower for k in ("grid import", "grid intake", "feeder", "transformer", "substation")):
        if hours:
            cap_kwh = parse_grid_cap(note_text)
            return DirectiveInterpretation(
                note_index=note_index,
                applies=True,
                directive_type="max_grid_window",
                structured_adjustment=MaxGridAdjustment(hours=hours, max_grid_kwh=cap_kwh),
                explanation=f"Grid import capped at {cap_kwh} kWh in hours {hours}.",
            )

    # 6. Default to no_op
    return DirectiveInterpretation(
        note_index=note_index,
        applies=False,
        directive_type="no_op",
        structured_adjustment=None,
        explanation="Note does not express an operational scheduling directive.",
    )
