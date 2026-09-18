import pytest
import httpx
from app.main import app
from app.models.request import ScenarioRequest, HourEntry, BatteryConfig
from app.models.directives import (
    DirectiveInterpretation,
    SolarReductionAdjustment,
    BatteryReserveAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
)
from app.rules.guardrails import validate_directives, GuardrailValidationError
from app.rules.directive_compiler import compile_directives
from app.validation.replay import replay_validate_plan, ReplayValidationError
from app.models.response import HourlyPlanEntry


def get_valid_baseline_payload():
    return {
        "scenario_id": "P0_BASELINE",
        "operator_notes": ["Regular operational schedule today."],
        "hours": [
            {
                "hour": h,
                "demand_kwh": 100.0,
                "solar_kwh": 30.0 if 8 <= h <= 16 else 0.0,
                "tariff_bdt_per_kwh": 6.0 if h < 6 or h >= 22 else 12.0,
            }
            for h in range(24)
        ],
        "battery": {
            "capacity_kwh": 200.0,
            "initial_energy_kwh": 100.0,
            "minimum_energy_kwh": 40.0,
            "max_charge_kwh_per_hour": 50.0,
            "max_discharge_kwh_per_hour": 50.0,
        },
    }


# ==============================================================================
# P0-01, P0-02, P0-03: Endpoint Availability & Baseline
# ==============================================================================
@pytest.mark.asyncio
async def test_p0_01_health_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_p0_03_valid_baseline_scenario():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json=get_valid_baseline_payload())
        assert resp.status_code == 200
        data = resp.json()
        assert data["scenario_id"] == "P0_BASELINE"
        assert len(data["hourly_plan"]) == 24
        assert len(data["directive_interpretation"]) == 1


# ==============================================================================
# P0-04 through P0-10: Hourly Record Sequence & Count Validation
# ==============================================================================
@pytest.mark.asyncio
async def test_p0_05_23_hours_rejected():
    payload = get_valid_baseline_payload()
    payload["hours"] = payload["hours"][:23]  # Only 23 hours
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json=payload)
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_p0_06_25_hours_rejected():
    payload = get_valid_baseline_payload()
    payload["hours"].append(payload["hours"][-1].copy())
    payload["hours"][-1]["hour"] = 24  # 25 hours
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json=payload)
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_p0_08_missing_hour_rejected():
    payload = get_valid_baseline_payload()
    payload["hours"][5]["hour"] = 6  # Skips hour 5, duplicates 6
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json=payload)
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_p0_10_out_of_order_hours_rejected():
    payload = get_valid_baseline_payload()
    payload["hours"][0], payload["hours"][1] = payload["hours"][1], payload["hours"][0]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json=payload)
        assert resp.status_code == 400


# ==============================================================================
# P0-11 through P0-16: Operator Notes Cardinality and Format
# ==============================================================================
@pytest.mark.asyncio
async def test_p0_11_12_valid_note_counts():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1 note
        p1 = get_valid_baseline_payload()
        p1["operator_notes"] = ["Note 1"]
        r1 = await client.post("/optimize-energy", json=p1)
        assert r1.status_code == 200

        # 3 notes
        p3 = get_valid_baseline_payload()
        p3["operator_notes"] = ["Note 1", "Note 2", "Note 3"]
        r3 = await client.post("/optimize-energy", json=p3)
        assert r3.status_code == 200


@pytest.mark.asyncio
async def test_p0_13_0_notes_rejected():
    payload = get_valid_baseline_payload()
    payload["operator_notes"] = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json=payload)
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_p0_14_4_notes_rejected():
    payload = get_valid_baseline_payload()
    payload["operator_notes"] = ["N1", "N2", "N3", "N4"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/optimize-energy", json=payload)
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_p0_15_16_empty_or_whitespace_note_rejected():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        p_empty = get_valid_baseline_payload()
        p_empty["operator_notes"] = [""]
        r_empty = await client.post("/optimize-energy", json=p_empty)
        assert r_empty.status_code == 400

        p_white = get_valid_baseline_payload()
        p_white["operator_notes"] = ["   \n\t  "]
        r_white = await client.post("/optimize-energy", json=p_white)
        assert r_white.status_code == 400


# ==============================================================================
# P0-19 through P0-24: Guardrails Directive Validation
# ==============================================================================
def test_p0_19_21_directive_count_mismatches():
    battery = BatteryConfig(
        capacity_kwh=200,
        initial_energy_kwh=100,
        minimum_energy_kwh=40,
        max_charge_kwh_per_hour=50,
        max_discharge_kwh_per_hour=50,
    )
    # Expected 2 directives, got 1
    d1 = [DirectiveInterpretation(note_index=0, applies=False, directive_type="no_op")]
    with pytest.raises(GuardrailValidationError):
        validate_directives(d1, num_notes=2, battery=battery)

    # Expected 1, got 2
    d2 = [
        DirectiveInterpretation(note_index=0, applies=False, directive_type="no_op"),
        DirectiveInterpretation(note_index=1, applies=False, directive_type="no_op"),
    ]
    with pytest.raises(GuardrailValidationError):
        validate_directives(d2, num_notes=1, battery=battery)


def test_p0_22_23_noop_constraints():
    # P0-22: no_op with applies=True rejected by schema validator
    with pytest.raises(ValueError):
        DirectiveInterpretation(note_index=0, applies=True, directive_type="no_op")

    # P0-23: no_op with structured_adjustment rejected by schema validator
    with pytest.raises(ValueError):
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type="no_op",
            structured_adjustment=HoursOnlyAdjustment(hours=[1, 2]),
        )


def test_p0_24_non_noop_with_applies_false():
    # P0-24: non-no-op with applies=False rejected by schema validator
    with pytest.raises(ValueError):
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type="no_charge_window",
            structured_adjustment=HoursOnlyAdjustment(hours=[14, 15]),
        )


# ==============================================================================
# P0-25 through P0-38: Replay Validation Invariants & Mutation Detection
# ==============================================================================
def get_valid_plan_and_scenario():
    scenario = ScenarioRequest(**get_valid_baseline_payload())
    compiled = compile_directives(hours=scenario.hours, battery=scenario.battery, directives=[])
    plan = [
        HourlyPlanEntry(
            hour=h,
            grid_kwh=70.0 if 8 <= h <= 16 else 100.0,
            solar_used_kwh=30.0 if 8 <= h <= 16 else 0.0,
            battery_action="idle",
            battery_kwh=0.0,
            battery_energy_after_kwh=100.0,
        )
        for h in range(24)
    ]
    total_grid = sum(e.grid_kwh for e in plan)
    total_cost = sum(e.grid_kwh * scenario.hours[e.hour].tariff_bdt_per_kwh for e in plan)
    peak_grid = max(e.grid_kwh for e in plan)
    return plan, scenario, compiled, total_grid, total_cost, peak_grid


def test_p0_25_neutrality_violation_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    # Battery discharges 10 in hour 0, then remains at 90 for rest of day:
    # State transitions are valid, but final energy ends at 90 != initial (100)
    plan[0].battery_action = "discharge"
    plan[0].battery_kwh = 10.0
    plan[0].battery_energy_after_kwh = 90.0
    plan[0].grid_kwh = 90.0
    for h in range(1, 24):
        plan[h].battery_energy_after_kwh = 90.0
    new_grid = sum(e.grid_kwh for e in plan)
    new_cost = sum(e.grid_kwh * scenario.hours[e.hour].tariff_bdt_per_kwh for e in plan)
    new_peak = max(e.grid_kwh for e in plan)
    with pytest.raises(ReplayValidationError, match="battery neutrality"):
        replay_validate_plan(plan, scenario, compiled, new_grid, new_cost, new_peak)


def test_p0_26_energy_balance_violation_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    # Mutate hour 5 grid without adjusting battery or solar
    plan[5].grid_kwh += 10.0
    with pytest.raises(ReplayValidationError, match="energy balance violated"):
        replay_validate_plan(plan, scenario, compiled, total_grid, total_cost, peak_grid)


def test_p0_27_solar_exceeds_effective_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    plan[10].solar_used_kwh = 35.0  # Forecast was 30
    with pytest.raises(ReplayValidationError, match="exceeds effective solar"):
        replay_validate_plan(plan, scenario, compiled, total_grid, total_cost, peak_grid)


def test_p0_28_reserve_violation_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    # Hour 0: discharge 45 -> energy = 55 (valid transition 100 - 45 = 55)
    plan[0].battery_action = "discharge"
    plan[0].battery_kwh = 45.0
    plan[0].grid_kwh = 55.0
    plan[0].battery_energy_after_kwh = 55.0
    # Hour 1: discharge 20 -> energy = 35 < reserve floor (40)
    plan[1].battery_action = "discharge"
    plan[1].battery_kwh = 20.0
    plan[1].grid_kwh = 80.0
    plan[1].battery_energy_after_kwh = 35.0
    with pytest.raises(ReplayValidationError, match="below reserve floor"):
        replay_validate_plan(plan, scenario, compiled, total_grid, total_cost, peak_grid)


def test_p0_29_capacity_violation_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    # Hour 0: charge 40 -> 140
    plan[0].battery_action = "charge"
    plan[0].battery_kwh = 40.0
    plan[0].grid_kwh = 140.0
    plan[0].battery_energy_after_kwh = 140.0
    # Hour 1: charge 40 -> 180
    plan[1].battery_action = "charge"
    plan[1].battery_kwh = 40.0
    plan[1].grid_kwh = 140.0
    plan[1].battery_energy_after_kwh = 180.0
    # Hour 2: charge 30 -> 210 > capacity (200)
    plan[2].battery_action = "charge"
    plan[2].battery_kwh = 30.0
    plan[2].grid_kwh = 130.0
    plan[2].battery_energy_after_kwh = 210.0
    with pytest.raises(ReplayValidationError, match="exceeds capacity"):
        replay_validate_plan(plan, scenario, compiled, total_grid, total_cost, peak_grid)


def test_p0_30_31_rate_limit_violations_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    # Charge rate violation
    plan[2].battery_action = "charge"
    plan[2].battery_kwh = 60.0  # Max charge is 50
    plan[2].grid_kwh = 160.0  # Balance
    plan[2].battery_energy_after_kwh = 160.0
    with pytest.raises(ReplayValidationError, match="exceeds max_charge"):
        replay_validate_plan(plan, scenario, compiled, total_grid, total_cost, peak_grid)


def test_p0_32_grid_cap_violation_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    compiled.grid_cap[10] = 50.0  # Cap at 50
    plan[10].grid_kwh = 70.0  # Exceeds cap
    with pytest.raises(ReplayValidationError, match="exceeds grid cap"):
        replay_validate_plan(plan, scenario, compiled, total_grid, total_cost, peak_grid)


def test_p0_33_34_negative_values_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    plan[0].grid_kwh = -1.0
    with pytest.raises(ReplayValidationError, match="negative grid"):
        replay_validate_plan(plan, scenario, compiled, total_grid, total_cost, peak_grid)


def test_p0_35_corrupted_aggregates_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    # Cost corrupted
    with pytest.raises(ReplayValidationError, match="Total cost BDT mismatch"):
        replay_validate_plan(plan, scenario, compiled, total_grid, total_cost + 10.0, peak_grid)


def test_p0_36_37_plan_length_mismatch_caught():
    plan, scenario, compiled, total_grid, total_cost, peak_grid = get_valid_plan_and_scenario()
    with pytest.raises(ReplayValidationError, match="must have 24 hours"):
        replay_validate_plan(plan[:23], scenario, compiled, total_grid, total_cost, peak_grid)
