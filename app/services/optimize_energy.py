import logging
from app.llm.interpreter import interpret_operator_notes
from app.models.request import ScenarioRequest
from app.models.response import OptimizeEnergyResponse
from app.optimizer.solve import solve_schedule
from app.rules.directive_compiler import compile_directives
from app.rules.guardrails import validate_directives
from app.validation.aggregates import compute_aggregates
from app.validation.replay import replay_validate_plan

logger = logging.getLogger("gridwise.services.optimize_energy")


def generate_plan_summary(directives: list, total_cost_bdt: float, total_grid_kwh: float) -> str:
    """Generates a concise, informative summary of applied directives and schedule."""
    active = [d.directive_type for d in directives if d.applies]
    if active:
        active_str = ", ".join(active)
        return (
            f"Optimized 24-hour schedule applying active directive(s): {active_str}. "
            f"Achieved total cost of {total_cost_bdt:.2f} BDT with {total_grid_kwh:.2f} kWh total grid import."
        )
    return (
        f"Optimized 24-hour baseline schedule with no active operational constraints. "
        f"Achieved total cost of {total_cost_bdt:.2f} BDT with {total_grid_kwh:.2f} kWh total grid import."
    )


async def process_scenario_optimization(scenario: ScenarioRequest) -> OptimizeEnergyResponse:
    """
    Executes the end-to-end GridWise optimization pipeline:
    1. LLM semantic interpretation (NVIDIA NIM Nemotron-3 Super 120B)
    2. Deterministic guardrails firewall
    3. Directive compiler (multi-directive hard constraints)
    4. Exact HiGHS MILP mathematical solver
    5. Direct recalculation of aggregates from hourly_plan
    6. Independent replay audit firewall
    7. Clean response assembly
    """
    logger.info(f"Processing scenario {scenario.scenario_id} with {len(scenario.operator_notes)} notes")

    # Step 1: Semantic interpretation
    raw_directives = await interpret_operator_notes(scenario.operator_notes, scenario.battery)

    # Step 2: Deterministic guardrails firewall
    validated_directives = validate_directives(
        raw_directives, len(scenario.operator_notes), scenario.battery
    )

    # Step 3: Directive compilation
    compiled = compile_directives(scenario.hours, scenario.battery, validated_directives)

    # Step 4: Mathematical optimization
    hourly_plan = solve_schedule(scenario.hours, scenario.battery, compiled)

    # Step 5: Direct recomputation of aggregates
    total_grid, total_cost, peak_grid = compute_aggregates(hourly_plan, scenario.hours)

    # Step 6: Independent replay audit firewall
    replay_validate_plan(
        plan=hourly_plan,
        scenario=scenario,
        directives=validated_directives,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
    )

    # Step 7: Build response
    summary = generate_plan_summary(validated_directives, total_cost, total_grid)

    return OptimizeEnergyResponse(
        scenario_id=scenario.scenario_id,
        directive_interpretation=validated_directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=summary,
    )
