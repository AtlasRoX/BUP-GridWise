from dataclasses import dataclass
from typing import List, Tuple
import numpy as np
from scipy.optimize import LinearConstraint, Bounds
from scipy.sparse import lil_matrix
from app.models.request import HourEntry, BatteryConfig
from app.rules.directive_compiler import CompiledConstraints

# Indices layout in decision vector x (size 168):
# 0..23:   G (grid import)
# 24..47:  S (solar used)
# 48..71:  C (battery charge)
# 72..95:  D (battery discharge)
# 96..119: E (battery energy after hour)
# 120..143: zC (charge binary)
# 144..167: zD (discharge binary)

NUM_HOURS = 24
IDX_G = 0
IDX_S = NUM_HOURS
IDX_C = NUM_HOURS * 2
IDX_D = NUM_HOURS * 3
IDX_E = NUM_HOURS * 4
IDX_ZC = NUM_HOURS * 5
IDX_ZD = NUM_HOURS * 6
NUM_VARS = NUM_HOURS * 7


@dataclass
class MILPProblem:
    c: np.ndarray
    integrality: np.ndarray
    bounds: Bounds
    constraints: LinearConstraint


def build_milp_problem(
    hours: List[HourEntry],
    battery: BatteryConfig,
    compiled: CompiledConstraints,
) -> MILPProblem:
    # 1. Objective: Minimize Sum(tariff[h] * G[h])
    c = np.zeros(NUM_VARS)
    for h in range(NUM_HOURS):
        c[IDX_G + h] = hours[h].tariff_bdt_per_kwh

    # 2. Integrality: 0 for continuous, 1 for integer/binary
    integrality = np.zeros(NUM_VARS)
    for h in range(NUM_HOURS):
        integrality[IDX_ZC + h] = 1
        integrality[IDX_ZD + h] = 1

    # 3. Variable Bounds
    lb = np.zeros(NUM_VARS)
    ub = np.full(NUM_VARS, np.inf)

    for h in range(NUM_HOURS):
        # G[h] >= 0, G[h] <= grid_cap[h]
        lb[IDX_G + h] = 0.0
        ub[IDX_G + h] = compiled.grid_cap[h]

        # S[h] >= 0, S[h] <= effective_solar[h]
        lb[IDX_S + h] = 0.0
        ub[IDX_S + h] = max(0.0, compiled.effective_solar[h])

        # C[h] >= 0, C[h] <= max_charge (or 0 if charge prohibited)
        lb[IDX_C + h] = 0.0
        if not compiled.charge_allowed[h]:
            ub[IDX_C + h] = 0.0
        else:
            ub[IDX_C + h] = battery.max_charge_kwh_per_hour

        # D[h] >= 0, D[h] <= max_discharge (or 0 if discharge prohibited)
        lb[IDX_D + h] = 0.0
        if not compiled.discharge_allowed[h]:
            ub[IDX_D + h] = 0.0
        else:
            ub[IDX_D + h] = battery.max_discharge_kwh_per_hour

        # E[h] >= reserve_floor[h], E[h] <= capacity
        lb[IDX_E + h] = compiled.reserve_floor[h]
        ub[IDX_E + h] = battery.capacity_kwh

        # zC[h] in {0, 1}
        lb[IDX_ZC + h] = 0.0
        ub[IDX_ZC + h] = 1.0

        # zD[h] in {0, 1}
        lb[IDX_ZD + h] = 0.0
        ub[IDX_ZD + h] = 1.0

    # Terminal battery neutrality: E[23] must equal initial_energy_kwh
    lb[IDX_E + 23] = battery.initial_energy_kwh
    ub[IDX_E + 23] = battery.initial_energy_kwh

    # 4. Linear Constraints
    # Total rows:
    # - 24 energy balance rows
    # - 24 battery state transition rows
    # - 24 charge indicator coupling rows (C[h] <= max_charge * zC[h])
    # - 24 discharge indicator coupling rows (D[h] <= max_discharge * zD[h])
    # - 24 mutual exclusivity rows (zC[h] + zD[h] <= 1)
    # Total = 120 constraint rows
    num_rows = NUM_HOURS * 5
    A = lil_matrix((num_rows, NUM_VARS), dtype=float)
    lhs = np.zeros(num_rows)
    rhs = np.zeros(num_rows)

    row_idx = 0

    # Constraint 1: Energy balance G[h] + S[h] + D[h] - C[h] = demand[h]
    for h in range(NUM_HOURS):
        A[row_idx, IDX_G + h] = 1.0
        A[row_idx, IDX_S + h] = 1.0
        A[row_idx, IDX_D + h] = 1.0
        A[row_idx, IDX_C + h] = -1.0
        lhs[row_idx] = hours[h].demand_kwh
        rhs[row_idx] = hours[h].demand_kwh
        row_idx += 1

    # Constraint 2: Battery dynamics
    # Hour 0: E[0] - C[0] + D[0] = initial_energy
    A[row_idx, IDX_E + 0] = 1.0
    A[row_idx, IDX_C + 0] = -1.0
    A[row_idx, IDX_D + 0] = 1.0
    lhs[row_idx] = battery.initial_energy_kwh
    rhs[row_idx] = battery.initial_energy_kwh
    row_idx += 1

    # Hours 1..23: E[h] - E[h-1] - C[h] + D[h] = 0
    for h in range(1, NUM_HOURS):
        A[row_idx, IDX_E + h] = 1.0
        A[row_idx, IDX_E + (h - 1)] = -1.0
        A[row_idx, IDX_C + h] = -1.0
        A[row_idx, IDX_D + h] = 1.0
        lhs[row_idx] = 0.0
        rhs[row_idx] = 0.0
        row_idx += 1

    # Constraint 3: Charge rate coupling C[h] - max_charge * zC[h] <= 0
    for h in range(NUM_HOURS):
        A[row_idx, IDX_C + h] = 1.0
        A[row_idx, IDX_ZC + h] = -battery.max_charge_kwh_per_hour
        lhs[row_idx] = -np.inf
        rhs[row_idx] = 0.0
        row_idx += 1

    # Constraint 4: Discharge rate coupling D[h] - max_discharge * zD[h] <= 0
    for h in range(NUM_HOURS):
        A[row_idx, IDX_D + h] = 1.0
        A[row_idx, IDX_ZD + h] = -battery.max_discharge_kwh_per_hour
        lhs[row_idx] = -np.inf
        rhs[row_idx] = 0.0
        row_idx += 1

    # Constraint 5: Mutual exclusivity zC[h] + zD[h] <= 1
    for h in range(NUM_HOURS):
        A[row_idx, IDX_ZC + h] = 1.0
        A[row_idx, IDX_ZD + h] = 1.0
        lhs[row_idx] = 0.0
        rhs[row_idx] = 1.0
        row_idx += 1

    linear_constraints = LinearConstraint(A.tocsc(), lhs, rhs)
    var_bounds = Bounds(lb, ub)

    return MILPProblem(
        c=c,
        integrality=integrality,
        bounds=var_bounds,
        constraints=linear_constraints,
    )
