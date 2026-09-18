import pytest
import httpx
from app.main import app
from app.models.request import HourEntry, BatteryConfig, ScenarioRequest


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_invalid_request_returns_400():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Invalid body: missing hours, invalid battery
        response = await client.post("/optimize-energy", json={"scenario_id": "bad"})
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "Bad Request"


from unittest.mock import patch
from app.models.directives import DirectiveInterpretation


@pytest.mark.asyncio
async def test_optimize_energy_valid():
    hours = [
        {"hour": h, "demand_kwh": 100.0, "solar_kwh": 20.0, "tariff_bdt_per_kwh": 10.0}
        for h in range(24)
    ]
    battery = {
        "capacity_kwh": 200.0,
        "initial_energy_kwh": 100.0,
        "minimum_energy_kwh": 40.0,
        "max_charge_kwh_per_hour": 50.0,
        "max_discharge_kwh_per_hour": 50.0,
    }
    payload = {
        "scenario_id": "test_api_01",
        "operator_notes": ["Routine campus activity today."],
        "hours": hours,
        "battery": battery,
    }

    mock_directive = [
        DirectiveInterpretation(
            note_index=0,
            applies=False,
            directive_type="no_op",
            structured_adjustment=None,
            explanation="Routine campus activity, no operational change.",
        )
    ]

    with patch("app.services.optimize_energy.interpret_operator_notes", return_value=mock_directive):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/optimize-energy", json=payload)
            assert response.status_code == 200
        data = response.json()
        assert data["scenario_id"] == "test_api_01"
        assert len(data["directive_interpretation"]) == 1
        assert len(data["hourly_plan"]) == 24
        assert "total_grid_kwh" in data
        assert "total_cost_bdt" in data
        assert "peak_grid_kwh" in data
        assert "plan_summary" in data
