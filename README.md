# GridWise — Smart Campus Energy Optimization Service

**BUP CSE Fest 2026 · Hackathon · Online Preliminary Round**  
**Stateless 24-Hour Energy Scheduling Microservice with LLM Operator Directive Interpretation**

---

## Architecture Overview

GridWise solves 24-hour campus electrical dispatch under dynamic electricity tariffs, intermittent rooftop solar generation, battery storage constraints, and natural-language operator directives.

### Core Architectural Principle
> **Human language must never directly become mathematical equations.**

The service operates as a strictly ordered, deterministic pipeline:

```
                  ┌────────────────────────────────────────────────┐
                  │         HTTP POST /optimize-energy             │
                  └───────────────────────┬────────────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────────┐
                  │   1. Request Validation Firewall (Pydantic v2) │
                  │      - Exactly 24 hours (0..23)                │
                  │      - 1..3 non-empty operator notes           │
                  │      - Finite non-negative numbers             │
                  └───────────────────────┬────────────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────────┐
                  │   2. LLM Semantic Interpreter                  │
                  │      - Model: NVIDIA NIM Nemotron-3-Super-120B │
                  │      - Prompt injects battery capacity context │
                  │      - Translates notes → structured schema    │
                  └───────────────────────┬────────────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────────┐
                  │   3. Deterministic Guardrails Firewall         │
                  │      - Enforces allowed directive types        │
                  │      - Verifies note_index order (0..N-1)      │
                  │      - Normalizes time windows & solar factor  │
                  └───────────────────────┬────────────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────────┐
                  │   4. Directive Compiler                        │
                  │      - Simultaneous multi-directive merging    │
                  │      - Generates per-hour constraint vectors   │
                  └───────────────────────┬────────────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────────┐
                  │   5. Exact HiGHS MILP Optimizer (SciPy)        │
                  │      - 168 variables, 120 constraint rows      │
                  │      - Mutual charge/discharge exclusivity     │
                  │      - Exact end-of-day battery neutrality     │
                  │      - Global mathematical optimum (<50ms)     │
                  └───────────────────────┬────────────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────────┐
                  │   6. Aggregate Recalculator                    │
                  │      - Direct computation from final plan      │
                  │      - Zero discrepancy in totals              │
                  └───────────────────────┬────────────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────────┐
                  │   7. Independent Replay Audit Firewall         │
                  │      - Hour-by-hour physical invariant check   │
                  │      - Energy balance & battery limits check   │
                  └───────────────────────┬────────────────────────┘
                                          │
                                          ▼
                  ┌────────────────────────────────────────────────┐
                  │         HTTP 200 JSON Response                 │
                  └────────────────────────────────────────────────┘
```

---

## Public Endpoints Contract

### Live Public Service URL (Render Deployment)
- **Base URL**: `https://gridwise-dbdi.onrender.com`
- **Health Check**: `https://gridwise-dbdi.onrender.com/health`
- **Optimize Endpoint**: `https://gridwise-dbdi.onrender.com/optimize-energy`

The microservice exposes two public HTTP endpoints:

| Endpoint | Method | Purpose | Status Code | Expected Response |
|---|---|---|---|---|
| `/health` | `GET` | Service readiness probe for judge harness | `200 OK` | `{"status": "ok"}` |
| `/optimize-energy` | `POST` | 24-hour scenario optimization | `200 OK` | Full JSON response matching challenge schema |

---

## Clean-Machine Local Quickstart

### Prerequisites
- Python 3.11+ (tested on Python 3.11, 3.12, 3.13, 3.14)
- Git

### 1. Clone & Set Up Virtual Environment
```bash
# Clone the repository
git clone https://github.com/AtlasRoX/BUP-GridWise.git
cd BUP-GridWise

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
# On Windows CMD:
.venv\Scripts\activate.bat
```

### 2. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env` and configure your credentials:
```bash
cp .env.example .env
```

Edit `.env`:
```ini
NVIDIA_NIM_API_KEY=nvapi-your-key-here
NVIDIA_NIM_MODEL=nvidia/nemotron-3-super-120b-a12b
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
PORT=8000
HOST=0.0.0.0
```
*(Note: If no API key is set, the service automatically engages its deterministic semantic fallback parser so local tests still succeed completely without external network access).*

### 4. Run the Service
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
The service will start, warm up the HiGHS solver in <5 seconds, and listen on `http://0.0.0.0:8000`.

---

## Verification & Testing

### Automated Test Suite
Run the full automated test suite with pytest:
```bash
pytest -v
```
Output: **28 passed in ~4 seconds** (covers API contracts, guardrails, multi-directive compiler, HiGHS MILP, replay audit, all 10 sample cases, and paraphrase stress tests).

### Public Sample Verification Script
Run the automated evaluation harness across all 10 public sample cases:
```bash
# In-process test:
python verify_solution.py

# Against a running server:
python verify_solution.py --base-url http://localhost:8000
```

#### Verification Benchmark Summary:
```
================================================================================
Running GridWise Verification on 10 Public Sample Cases
Target: In-Process ASGI Test Client (no external server required)
[+] /health check PASSED (status: ok)

Case ID      Status     Team Cost (BDT)    Ref Cost (BDT)     Quality Ratio  
--------------------------------------------------------------------------------
SAMPLE-01    PASS       38365.00           38365.00           1.0000         
SAMPLE-02    PASS       42885.00           42885.00           1.0000         
SAMPLE-03    PASS       35480.00           35480.00           1.0000         
SAMPLE-04    PASS       40495.00           40495.00           1.0000         
SAMPLE-05    PASS       33950.00           33950.00           1.0000         
SAMPLE-06    PASS       34090.00           34090.00           1.0000         
SAMPLE-07    PASS       38550.00           38550.00           1.0000         
SAMPLE-08    PASS       37665.00           37665.00           1.0000         
SAMPLE-09    PASS       34873.00           34873.00           1.0000         
SAMPLE-10    PASS       41620.00           41620.00           1.0000         
--------------------------------------------------------------------------------
Average Quality Ratio: 1.0000 | Optimization Score: 10.00 / 10.00
Overall Result: ALL CHECKS PASSED
================================================================================
```

---

## Sample cURL Commands

### 1. Health Probe
```bash
curl -X GET http://localhost:8000/health
```
**Expected Response:**
```json
{"status":"ok"}
```

### 2. Sample Energy Optimization Request (SAMPLE-01)
```bash
curl -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_id": "SAMPLE-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
      "The sports office moved next month\u0027s registration deadline."
    ],
    "hours": [
      {"hour": 0, "demand_kwh": 90, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 1, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 2, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 3, "demand_kwh": 80, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 4, "demand_kwh": 85, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 5, "demand_kwh": 95, "solar_kwh": 0, "tariff_bdt_per_kwh": 6},
      {"hour": 6, "demand_kwh": 110, "solar_kwh": 10, "tariff_bdt_per_kwh": 9},
      {"hour": 7, "demand_kwh": 130, "solar_kwh": 35, "tariff_bdt_per_kwh": 9},
      {"hour": 8, "demand_kwh": 160, "solar_kwh": 80, "tariff_bdt_per_kwh": 9},
      {"hour": 9, "demand_kwh": 190, "solar_kwh": 130, "tariff_bdt_per_kwh": 9},
      {"hour": 10, "demand_kwh": 210, "solar_kwh": 170, "tariff_bdt_per_kwh": 9},
      {"hour": 11, "demand_kwh": 220, "solar_kwh": 200, "tariff_bdt_per_kwh": 9},
      {"hour": 12, "demand_kwh": 215, "solar_kwh": 210, "tariff_bdt_per_kwh": 9},
      {"hour": 13, "demand_kwh": 210, "solar_kwh": 190, "tariff_bdt_per_kwh": 9},
      {"hour": 14, "demand_kwh": 200, "solar_kwh": 160, "tariff_bdt_per_kwh": 9},
      {"hour": 15, "demand_kwh": 185, "solar_kwh": 110, "tariff_bdt_per_kwh": 9},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 60, "tariff_bdt_per_kwh": 9},
      {"hour": 17, "demand_kwh": 165, "solar_kwh": 20, "tariff_bdt_per_kwh": 14},
      {"hour": 18, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 19, "demand_kwh": 200, "solar_kwh": 0, "tariff_bdt_per_kwh": 22},
      {"hour": 20, "demand_kwh": 190, "solar_kwh": 0, "tariff_bdt_per_kwh": 22},
      {"hour": 21, "demand_kwh": 170, "solar_kwh": 0, "tariff_bdt_per_kwh": 18},
      {"hour": 22, "demand_kwh": 140, "solar_kwh": 0, "tariff_bdt_per_kwh": 14},
      {"hour": 23, "demand_kwh": 110, "solar_kwh": 0, "tariff_bdt_per_kwh": 6}
    ],
    "battery": {
      "capacity_kwh": 200,
      "initial_energy_kwh": 100,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 50,
      "max_discharge_kwh_per_hour": 50
    }
  }'
```

---

## Docker Deployment & Fallback Image

### 1. Build the Container Image
```bash
docker build -t gridwise-service:latest .
```

### 2. Run the Container
```bash
docker run -d \
  -p 8000:8000 \
  -e NVIDIA_NIM_API_KEY="your-key-here" \
  --name gridwise \
  gridwise-service:latest
```

### 3. Test Container
```bash
curl http://localhost:8000/health
```

---

## Cloud Deployment (Render.com)

The service is configured for zero-downtime deployment on Render:

### Method A: Blueprint Deployment (render.yaml)
1. Push this repository to GitHub.
2. Log into [Render Dashboard](https://dashboard.render.com).
3. Click **New +** → **Blueprint**.
4. Select your GitHub repository; Render will automatically detect `render.yaml`.
5. Under Environment Variables, enter your `NVIDIA_NIM_API_KEY`.
6. Click **Apply** to deploy the service.

### Method B: Manual Web Service Deployment
1. Log into [Render Dashboard](https://dashboard.render.com).
2. Click **New +** → **Web Service**.
3. Connect your GitHub repository.
4. Set **Runtime** to **Docker**.
5. Set **Health Check Path** to `/health`.
6. Under **Environment Variables**, add:
   - `NVIDIA_NIM_API_KEY`: `your-nvapi-key`
   - `NVIDIA_NIM_MODEL`: `nvidia/nemotron-3-super-120b-a12b`
   - `NVIDIA_NIM_BASE_URL`: `https://integrate.api.nvidia.com/v1`
7. Click **Create Web Service**. Render dynamically binds `$PORT` (default 10000) and exposes the public HTTPS URL.

---

## Tie-Breaker Deliverable: 3-Minute Solution Video

The complete, word-for-word 3-minute presentation script and slide outline is available in:
📄 [`video_script.md`](video_script.md)

It covers:
1. **0:00 - 0:30**: Problem understanding & decoupled 5-stage architecture.
2. **0:30 - 1:15**: NVIDIA NIM Nemotron semantic interpretation & guardrails.
3. **1:15 - 2:00**: Exact 168-variable HiGHS MILP optimization model.
4. **2:00 - 2:30**: Independent replay validation firewall & recalculated totals.
5. **2:30 - 3:00**: Live Render deployment & test suite verification.

---

## Mathematical Model Details

The 24-hour dispatch problem is solved as a **Mixed-Integer Linear Program (MILP)**:
- **Decision Variables** (168 total):
  - $G[h] \ge 0$: Grid import (continuous)
  - $S[h] \ge 0$: Solar consumed (continuous)
  - $C[h] \ge 0$: Battery charge (continuous)
  - $D[h] \ge 0$: Battery discharge (continuous)
  - $E[h] \ge 0$: Battery stored energy at end of hour (continuous)
  - $z_C[h] \in \{0, 1\}$: Binary charge mode indicator
  - $z_D[h] \in \{0, 1\}$: Binary discharge mode indicator
- **Objective**:
  $$\min \sum_{h=0}^{23} G[h] \times \text{tariff}[h]$$
- **Constraints**:
  1. Energy balance: $G[h] + S[h] + D[h] = \text{demand}[h] + C[h]$
  2. Solar bound: $0 \le S[h] \le \text{effective\_solar}[h]$
  3. Storage evolution: $E[h] = E[h-1] + C[h] - D[h]$ with $E[-1] = \text{initial\_energy}$
  4. Bounds: $\text{reserve\_floor}[h] \le E[h] \le \text{capacity}$
  5. Rate limits: $C[h] \le \text{max\_charge} \times z_C[h]$, $D[h] \le \text{max\_discharge} \times z_D[h]$
  6. Action exclusivity: $z_C[h] + z_D[h] \le 1$
  7. End-of-day neutrality: $E[23] = \text{initial\_energy}$
  8. Directive bounds: $C[h] = 0$ in no-charge hours, $D[h] = 0$ in no-discharge hours, $G[h] \le \text{grid\_cap}[h]$.

---

## Security, Limitations & Secret Handling

- **No Secrets in Code/Images**: No API keys, passwords, or credentials are baked into the Docker image or committed to git.
- **Synthetic Data**: The service operates exclusively on synthetic challenge payloads supplied by the judging harness.
- **Log Sanitization**: Error handlers catch all exceptions and return sanitized messages without leaking credentials, environment variables, or internal stack traces.
- **Provider Quota**: When utilizing NVIDIA NIM, teams manage their own rate limits. The service includes automated retry logic with exponential backoff.
