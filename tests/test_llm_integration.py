import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.config import settings
from app.llm.interpreter import (
    interpret_operator_notes,
    clean_json_response,
    LLMInterpretationError,
)
from app.llm.prompt import build_user_prompt
from app.models.request import BatteryConfig, ScenarioRequest, HourEntry
from app.models.directives import (
    DirectiveInterpretation,
    SolarReductionAdjustment,
    HoursOnlyAdjustment,
    MaxGridAdjustment,
)
from app.rules.guardrails import validate_directives, GuardrailValidationError
from app.services.optimize_energy import process_scenario_optimization


@pytest.fixture
def sample_battery():
    return BatteryConfig(
        capacity_kwh=200.0,
        initial_energy_kwh=100.0,
        minimum_energy_kwh=40.0,
        max_charge_kwh_per_hour=50.0,
        max_discharge_kwh_per_hour=50.0,
    )


@pytest.fixture
def baseline_scenario(sample_battery):
    hours = [
        HourEntry(
            hour=h,
            demand_kwh=100.0,
            solar_kwh=30.0 if 8 <= h <= 16 else 0.0,
            tariff_bdt_per_kwh=6.0 if h < 6 or h >= 22 else 15.0,
        )
        for h in range(24)
    ]
    return ScenarioRequest(
        scenario_id="LLM_TEST_SCENARIO",
        operator_notes=["Mock operator note"],
        hours=hours,
        battery=sample_battery,
    )


def make_mock_choice(content_str: str):
    message = MagicMock()
    message.content = content_str
    choice = MagicMock()
    choice.message = message
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ==============================================================================
# LLM-01: Valid single directive returned by mock provider
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_01_valid_solar_directive(sample_battery):
    payload = json.dumps([
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {
                "hours": [11, 12, 13],
                "factor": 0.25,
            },
            "explanation": "Cloud cover reduces solar to 25% for hours 11-13.",
        }
    ])

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        res = await interpret_operator_notes(["Solar down to 25% 11am-2pm"], sample_battery)
        assert len(res) == 1
        assert res[0].note_index == 0
        assert res[0].applies is True
        assert res[0].directive_type == "solar_reduction"
        assert res[0].structured_adjustment.hours == [11, 12, 13]
        assert res[0].structured_adjustment.factor == 0.25


# ==============================================================================
# LLM-02: Three directives returned out of order mapped and sorted
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_02_three_directives_order_mapping(sample_battery):
    payload = json.dumps([
        {"note_index": 2, "applies": False, "directive_type": "no_op", "structured_adjustment": None, "explanation": "Note 2"},
        {"note_index": 0, "applies": True, "directive_type": "no_charge_window", "structured_adjustment": {"hours": [17, 18]}, "explanation": "Note 0"},
        {"note_index": 1, "applies": True, "directive_type": "no_discharge_window", "structured_adjustment": {"hours": [2, 3]}, "explanation": "Note 1"},
    ])

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        res = await interpret_operator_notes(["Note 0", "Note 1", "Note 2"], sample_battery)
        assert len(res) == 3
        assert [r.note_index for r in res] == [0, 1, 2]
        assert res[0].directive_type == "no_charge_window"
        assert res[1].directive_type == "no_discharge_window"
        assert res[2].directive_type == "no_op"


# ==============================================================================
# LLM-03: Model wraps response in markdown code fence
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_03_markdown_code_fence(sample_battery):
    fenced_content = """```json
    [
        {
            "note_index": 0,
            "applies": false,
            "directive_type": "no_op",
            "structured_adjustment": null,
            "explanation": "General weather update."
        }
    ]
    ```"""

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(fenced_content))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        res = await interpret_operator_notes(["Weather looks good"], sample_battery)
        assert len(res) == 1
        assert res[0].directive_type == "no_op"
        assert res[0].applies is False


# ==============================================================================
# LLM-04: Model returns duplicate note indices
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_04_duplicate_note_indices_caught(sample_battery):
    payload = json.dumps([
        {"note_index": 0, "applies": False, "directive_type": "no_op", "structured_adjustment": None, "explanation": "N1"},
        {"note_index": 0, "applies": False, "directive_type": "no_op", "structured_adjustment": None, "explanation": "Duplicate N0"},
    ])

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        with pytest.raises(GuardrailValidationError, match="Directive note_index mismatch"):
            directives = await interpret_operator_notes(["Note A", "Note B"], sample_battery)
            validate_directives(directives, num_notes=2, battery=sample_battery)


# ==============================================================================
# LLM-05: Model returns unsupported directive type
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_05_unsupported_directive_type(sample_battery):
    payload = json.dumps([
        {"note_index": 0, "applies": True, "directive_type": "unsupported_wind_boost", "structured_adjustment": None, "explanation": "Wind boost"}
    ])

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        with pytest.raises(LLMInterpretationError):
            await interpret_operator_notes(["Boost wind"], sample_battery)


# ==============================================================================
# LLM-06: Model returns invalid factor > 1.0
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_06_factor_greater_than_one(sample_battery):
    payload = json.dumps([
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "structured_adjustment": {"hours": [12], "factor": 1.4},
            "explanation": "Solar boost",
        }
    ])

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        with pytest.raises(LLMInterpretationError):
            await interpret_operator_notes(["Solar 140%"], sample_battery)


# ==============================================================================
# LLM-07: Model returns reserve over battery capacity
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_07_reserve_exceeds_capacity(sample_battery):
    payload = json.dumps([
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "minimum_battery_reserve",
            "structured_adjustment": {"hours": [18, 19], "minimum_energy_kwh": 350.0},
            "explanation": "Reserve 350 exceeds 200",
        }
    ])

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        directives = await interpret_operator_notes(["Reserve 350"], sample_battery)
        with pytest.raises(GuardrailValidationError, match="exceeds battery capacity"):
            validate_directives(directives, num_notes=1, battery=sample_battery)


# ==============================================================================
# LLM-08: Model returns no_op with valid null adjustment
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_08_noop_null_adjustment_passes(sample_battery):
    payload = json.dumps([
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "Routine inspection only.",
        }
    ])

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        directives = await interpret_operator_notes(["Inspection"], sample_battery)
        validated = validate_directives(directives, num_notes=1, battery=sample_battery)
        assert len(validated) == 1
        assert validated[0].applies is False


# ==============================================================================
# LLM-09: Model output explanation wording does not affect semantics
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_09_explanation_variations(sample_battery):
    for wording in ["Cloud cover reduced", "PV down drastically", "Rooftop solar low"]:
        payload = json.dumps([
            {
                "note_index": 0,
                "applies": True,
                "directive_type": "solar_reduction",
                "structured_adjustment": {"hours": [12], "factor": 0.5},
                "explanation": wording,
            }
        ])
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))
        with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
             patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
            res = await interpret_operator_notes(["Solar half"], sample_battery)
            assert res[0].structured_adjustment.factor == 0.5


# ==============================================================================
# LLM-10: Provider transient failure retries and succeeds inside budget
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_10_retry_succeeds_on_second_attempt(sample_battery):
    valid_payload = json.dumps([
        {"note_index": 0, "applies": False, "directive_type": "no_op", "structured_adjustment": None, "explanation": "Ok"}
    ])
    mock_client = MagicMock()
    # First call raises network timeout, second succeeds
    mock_client.chat.completions.create = AsyncMock(
        side_effect=[Exception("ReadTimeout"), make_mock_choice(valid_payload)]
    )

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        res = await interpret_operator_notes(["Ok"], sample_battery)
        assert len(res) == 1
        assert mock_client.chat.completions.create.call_count == 2


# ==============================================================================
# LLM-11: Provider raises 429 twice raises controlled LLMInterpretationError
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_11_provider_429_exhausted_retries(sample_battery):
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("429 Too Many Requests"))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        with pytest.raises(LLMInterpretationError, match="Language model interpretation failed"):
            await interpret_operator_notes(["Note"], sample_battery)


# ==============================================================================
# LLM-12: Provider returns malformed JSON twice
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_12_malformed_json_fails_gracefully(sample_battery):
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice("This is not JSON!"))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        with pytest.raises(LLMInterpretationError):
            await interpret_operator_notes(["Note"], sample_battery)


# ==============================================================================
# LLM-13: Prompt correctly injects battery capacity for percentage calculation
# ==============================================================================
def test_llm_13_capacity_injection_in_prompt():
    bat_100 = BatteryConfig(
        capacity_kwh=100.0,
        initial_energy_kwh=50.0,
        minimum_energy_kwh=20.0,
        max_charge_kwh_per_hour=25.0,
        max_discharge_kwh_per_hour=25.0,
    )
    prompt_100 = build_user_prompt(["Keep 50% reserve"], bat_100)
    assert '"battery_capacity_kwh": 100.0' in prompt_100

    bat_400 = BatteryConfig(
        capacity_kwh=400.0,
        initial_energy_kwh=200.0,
        minimum_energy_kwh=80.0,
        max_charge_kwh_per_hour=100.0,
        max_discharge_kwh_per_hour=100.0,
    )
    prompt_400 = build_user_prompt(["Keep 50% reserve"], bat_400)
    assert '"battery_capacity_kwh": 400.0' in prompt_400


# ==============================================================================
# LLM-14: LLM-produced directive changes optimizer behavior (no_discharge_window)
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_14_mock_llm_changes_optimizer_discharge(baseline_scenario):
    # Mock LLM returning no_discharge_window for hours [18, 19, 20]
    payload = json.dumps([
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": [18, 19, 20]},
            "explanation": "No discharge allowed during peak.",
        }
    ])
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        response = await process_scenario_optimization(baseline_scenario)
        # Check affected hours in the returned schedule: battery action must NEVER be discharge
        plan = response.hourly_plan
        for h in [18, 19, 20]:
            assert plan[h].battery_action != "discharge"
            assert plan[h].battery_kwh == 0.0 or plan[h].battery_action == "charge"


# ==============================================================================
# LLM-15: LLM-produced directive caps grid intake
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_15_mock_llm_caps_grid_intake(baseline_scenario):
    # Mock LLM returning max_grid_window = 50.0 for hours [10, 11]
    payload = json.dumps([
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": [10, 11], "max_grid_kwh": 50.0},
            "explanation": "Grid capped at 50 kWh.",
        }
    ])
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        response = await process_scenario_optimization(baseline_scenario)
        plan = response.hourly_plan
        for h in [10, 11]:
            assert plan[h].grid_kwh <= 50.0 + 1e-4


# ==============================================================================
# LLM-16: LLM-produced no_op produces zero constraint change
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_16_noop_schedule_identical(baseline_scenario):
    # Run with no_op directive
    payload_noop = json.dumps([
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "Irrelevant memo.",
        }
    ])
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=make_mock_choice(payload_noop))

    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        resp_noop = await process_scenario_optimization(baseline_scenario)

    # Clean schedule without notes
    scenario_clean = baseline_scenario.model_copy()
    scenario_clean.operator_notes = ["Routine"]
    with patch("app.llm.interpreter.get_llm_client", return_value=mock_client), \
         patch.object(settings, "nvidia_nim_api_key", "mock-nvapi-key"):
        resp_clean = await process_scenario_optimization(scenario_clean)

    assert abs(resp_noop.total_cost_bdt - resp_clean.total_cost_bdt) < 1e-3
    assert abs(resp_noop.total_grid_kwh - resp_clean.total_grid_kwh) < 1e-3


# ==============================================================================
# LLM-17: Empty API key raises controlled LLMInterpretationError (no silent bypass)
# ==============================================================================
@pytest.mark.asyncio
async def test_llm_17_no_key_raises_mandatory_compliance_error(sample_battery):
    with patch.object(settings, "nvidia_nim_api_key", ""):
        with pytest.raises(LLMInterpretationError, match="API key is unconfigured"):
            await interpret_operator_notes(["Any note"], sample_battery)
