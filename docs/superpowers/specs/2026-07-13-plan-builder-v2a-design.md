# RunFlow v2a — 5K Adaptive Plan Builder

**Date:** 2026-07-13
**Status:** Approved for planning
**Branch:** `plan-builder-v2a`
**Builds on:** v1 Training (fitness model / gray-zone / guardrails), HR-seeded history (288 runs)

## Goal

Let the runner set a **5K goal**, see a **realistic projected target time at multiple horizons** (8/10/12 weeks) grounded in their actual fitness, pick one, and get a **curated week-by-week plan** that respects the v1 injury-prevention guardrails — with a short **LLM-written coaching narrative** on top.

## Locked decisions

- **Distance:** 5K only (engine generalizes to 3K/10K/HM later; 100m sprint is a separate future slice).
- **Generation approach:** deterministic engine (guardrail-compliant *by construction*), LLM used **only** for the human coaching voice. Guardrails always win; the LLM never emits safety-critical numbers.
- **Target selection:** horizon-driven — app suggests targets at fixed horizons; user picks.
- **Ship together:** deterministic core + LLM narrative in the same release.
- **LLM provider:** OpenRouter (`OPENROUTER_API_KEY` already on the VM, validated). Target model: a strong Claude model via OpenRouter (`anthropic/claude-sonnet-4-5`); confirm exact id against the claude-api skill at build time.

## Non-goals (explicitly deferred)

- **v2b:** adherence scoring + adaptive re-planning (needs plans to exist first).
- Sprint/100m plans (separate engine, later).
- Per-workout manual editing/overrides (small add later).
- Calendar rescheduling / day-type awareness (v2b).

## Architecture

### Backend modules
- `fitness_projection.py` (pure) — project current 5K time + trainable targets at horizons.
- `plan_generator.py` (pure) — deterministic week-by-week 5K plan from the fitness model + guardrails.
- `coach_llm.py` — thin OpenRouter client for the narrative. Fail-safe: if the key/endpoint is unavailable, the plan still works; narrative is optional.
- New models in `models.py`: `Plan`, `PlannedWorkout`.
- Migration in `database.py` (create tables; the existing idempotent pattern).
- Endpoints in `main.py` (see below).

### 1. Target projection (`fitness_projection.py`)
- **Current 5K estimate** via Riegel: `T2 = T1 * (D2/D1)^1.06`, using the best recent sustained effort (prefer a real ≥3 km run in the last 90 days; else best-effort table). Guard when data is thin.
- **Trainable improvement:** cap realistic improvement at ~**0.5–0.8%/week** of race time for a returning runner on an 80/20 plan, applied over the horizon. Produces `{weeks, target_time_sec, improvement_pct}` for horizons **8, 10, 12** weeks.
- **Reality check (F6):** given a user-typed target, if it's faster than the trainable projection for that horizon, return `realistic: false` + the realistic range so the UI can warn.
- Pure functions, unit-tested against synthetic + the real fitness numbers.

### 2. Plan generation (`plan_generator.py`)
Input: fitness model (from `fitness_model.build_fitness_model`), chosen `{weeks, target_time_sec}`, `start_date`. Output: list of `PlannedWorkout` dicts. Rules (all from v1 guardrails, so every workout is safe by construction):
- **3–4 runs/week**, **~80% easy** (easy pace band + HR ceiling from the fitness model).
- **Long run** starts at `min(current_longest_28d, safe_start)` and grows **≤1 km/week**, capped so it never exceeds 5 km for a 5K plan.
- **Down week every 4th** (reduce volume ~30%).
- **One quality session/week** introduced **only once** the easy-run base supports it (≥ N weeks in, and volume threshold), never on back-to-back days with the long run.
- **Taper** the final week; goal-day workout = the 5K.
- Each workout: `date, week_number, day_type (easy|long|quality|strides|rest), target_distance_m, pace_low/high_sec, hr_ceiling, title, description`.

### 3. Coaching voice (`coach_llm.py`)
- After the deterministic plan exists, one OpenRouter chat call produces: a **plan overview** paragraph + a **one-line focus per week**. The prompt is given the plan + fitness context as data; it only *phrases* — it must not change numbers.
- Reads `OPENROUTER_API_KEY` via `config.py`. Model id resolved via the claude-api skill.
- **Fail-safe:** any error → return `None`; the plan is fully usable without narrative. Never blocks plan creation.

### 4. Data model
- **Plan**: `id, goal_type ('5k'), goal_distance_m, target_time_sec, start_date, goal_date, weeks, status ('active'|'completed'|'abandoned'), created_at, fitness_snapshot (JSON), narrative (JSON|null)`.
- **PlannedWorkout**: `id, plan_id (FK), date, week_number, day_type, target_distance_m, pace_low_sec, pace_high_sec, hr_ceiling, title, description`. (v2b will add `actual_activity_id`, `adherence`.)
- Only one `active` plan at a time (creating a new one marks the old `abandoned`).

### 5. Endpoints (`main.py`)
- `GET /api/plan/projections` — current 5K estimate + targets at 8/10/12 weeks.
- `POST /api/plan` — body `{weeks, target_time_sec}`; generates + stores plan + workouts + narrative; returns the plan.
- `GET /api/plan` — active plan + its workouts (or `null`).
- `DELETE /api/plan/{id}` — abandon a plan.

### 6. Frontend (Training page — new "Plan" section)
- **No active plan:** a "Build a 5K plan" panel → shows projected targets at 8/10/12 weeks (each: target time + implied paces) → user picks → `POST /api/plan` → plan appears.
- **Active plan:** header (goal, target, goal date, week X of N) + narrative overview + a **week-by-week calendar** of workouts (day_type-colored, distance + pace band + HR ceiling + title). Each planned run shows its "why" (description). An "abandon plan" control.

## Error handling
- Thin fitness data (few runs) → projection returns a low-confidence flag; UI shows a caveat.
- LLM failure → plan still created, narrative omitted (UI hides the narrative block).
- No fitness/HR data → still generates a conservative plan from volume/pace alone.

## Testing / verification
- `fitness_projection` + `plan_generator` fully unit-tested (deterministic; synthetic + real numbers).
- `coach_llm` tested with a mocked transport (no network in tests).
- End-to-end verified on live: build a plan from the real fitness model, confirm the calendar + narrative render, confirm guardrails hold (no long-run jump, easy days at easy pace/HR).

## Rollout order
1. Data model + migration.
2. `fitness_projection` (+ tests).
3. `plan_generator` (+ tests).
4. Endpoints (projections, create, get, delete).
5. `coach_llm` narrative (+ mocked test), wired into create.
6. Frontend Plan section (projection selector + calendar).
7. Deploy; verify on live.
