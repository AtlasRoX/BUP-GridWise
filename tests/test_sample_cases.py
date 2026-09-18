import json
import os
import pytest
from app.models.request import ScenarioRequest
from app.services.optimize_energy import process_scenario_optimization

SAMPLE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json",
)


def load_sample_cases():
    with open(SAMPLE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["cases"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", load_sample_cases(), ids=lambda c: c["id"])
async def test_public_sample_case(case):
    case_id = case["id"]
    case_input = case["input"]
    expected_output = case["expected_output"]

    scenario = ScenarioRequest.model_validate(case_input)
    response = await process_scenario_optimization(scenario)

    # 1. Check scenario_id echo
    assert response.scenario_id == case_input["scenario_id"]

    # 2. Check directive interpretation length and order
    assert len(response.directive_interpretation) == len(expected_output["directive_interpretation"])

    for i, expected_interp in enumerate(expected_output["directive_interpretation"]):
        actual_interp = response.directive_interpretation[i]
        assert actual_interp.note_index == expected_interp["note_index"]
        assert actual_interp.applies == expected_interp["applies"]
        assert actual_interp.directive_type == expected_interp["directive_type"]

        if expected_interp["structured_adjustment"] is not None:
            assert actual_interp.structured_adjustment is not None
            exp_adj = expected_interp["structured_adjustment"]
            act_adj = actual_interp.structured_adjustment.model_dump()

            assert act_adj["hours"] == exp_adj["hours"]
            if "factor" in exp_adj:
                assert abs(act_adj["factor"] - exp_adj["factor"]) < 0.01
            if "minimum_energy_kwh" in exp_adj:
                assert abs(act_adj["minimum_energy_kwh"] - exp_adj["minimum_energy_kwh"]) < 0.01
            if "max_grid_kwh" in exp_adj:
                assert abs(act_adj["max_grid_kwh"] - exp_adj["max_grid_kwh"]) < 0.01
        else:
            assert actual_interp.structured_adjustment is None

    # 3. Check optimization quality ratio against reference cost
    ref_cost = expected_output["total_cost_bdt"]
    team_cost = response.total_cost_bdt

    quality_ratio = min(1.0, ref_cost / team_cost)
    # The team cost should match the reference optimal cost within 1%
    assert quality_ratio >= 0.99, f"Case {case_id}: quality ratio {quality_ratio:.4f} too low (team={team_cost}, ref={ref_cost})"
    assert abs(team_cost - ref_cost) / ref_cost < 0.02, f"Case {case_id}: cost deviation > 2%"
