# 100m Sprint Plan Engine — Design

Sibling to the 5K plan engine. Same architecture (deterministic generator + LLM voice,
reusing `Plan`/`PlannedWorkout` + the Training page), but sprint-specific: **data-grounded
baseline** (extracted from the user's own interval history / best efforts), a **diagnosis**
that drives plan emphasis, sprint session types, and **tracking that reuses the existing
interval detector**.

Build order: **S1** (baseline+diagnosis+projection+generator+narrative+create-flow+rendering),
then **S2** (tracking: auto-match interval sessions + best-100m progress).

## Grounding facts (this user)
- Best 100m ever **19.0s** (Apr 25 2026), cluster 20–22s across 205 extracted 100m segments.
- Best 200m **70s**. 5 tagged interval sessions (150/250/400m reps).
- Diagnosis: **speed-endurance limited** — strong early (33s first 150m rep) but fades hard
  (46s by rep 6; fade ≈ +8.7s, decay ≈ 2.6s/rep). A 100m is lost in the last 40m → this is the lever.

## Schema (done; backward-compatible, nullable)
- `Plan.sprint_target_sec: float|None` — sub-second 100m target. 5K keeps `target_time_sec` (int).
  Sprint plans set `goal_type='sprint_100m'`, `goal_distance_m=100`, and store the sprint profile in
  the existing `fitness_snapshot` JSON.
- `PlannedWorkout.structure: JSON|None` — sprint rep scheme (see shape below). 5K workouts leave null.
- `_MIGRATIONS` updated (idempotent ALTER on startup).

## Module interfaces (pure functions; tested in isolation)

### sprint_baseline.py
```
build_sprint_profile(best_efforts, interval_configs, now) -> profile
  best_efforts:     [{"distance_target": int, "time_seconds": float, "start_date": datetime}]
  interval_configs: [{"start_date": datetime, "config": dict}]   # Activity.interval_config for is_interval rows
profile = {
  "best_100m_sec": float|None, "best_100m_date": str|None,
  "best_200m_sec": float|None,
  "top_speed_mps": float|None,          # 100 / best_100m_sec (avg, labelled as such)
  "interval_sessions": int,
  "fade_pct": float|None,               # avg (slowest_rep_pace/fastest_rep_pace - 1)*100 over sessions w/ >=3 reps
  "decay_sec_per_rep": float|None,
  "prior_speed_exposure": bool,         # >=2 interval sessions within ~365d
  "diagnosis": str,                     # speed_endurance | acceleration | top_speed | balanced | insufficient_data
  "diagnosis_detail": str,              # one human sentence, references the numbers
  "supporting_efforts": [{"distance_m": int, "time_sec": float, "date": str}],  # up to ~4 best short efforts, for display
}
```
Diagnosis rules (first match): no 100m & no intervals -> insufficient_data; fade_pct>=20 (>=1 session) ->
speed_endurance; top_speed_mps<5.2 -> top_speed; else balanced. best_100m within last 365d preferred, else all-time.

### sprint_projection.py
```
sprint_projections(current_100m_sec, now, horizons=(8,10,12)) -> {
  "current_100m_sec": float,
  "horizons": [{"weeks": int, "target_100m_sec": float, "improvement_pct": float}]   # target rounded 0.1
}
sprint_reality_check(current_100m_sec, target_100m_sec, weeks) -> {"realistic": bool, "floor_100m_sec": float, "note": str}
```
Model: PER_WEEK=0.009, CAP=0.12. imp=min(CAP, PER_WEEK*weeks). target=round(current*(1-imp),1).
floor=round(current*(1-imp),1). realistic = target_100m_sec >= floor - 1e-9.

### sprint_plan_generator.py
```
generate_sprint_plan(profile, weeks, target_100m_sec, start_date, days=(0,2,5)) -> {
  "goal_date": datetime, "goal_100m_sec": float, "workouts": [wo]
}
wo = {
  "date": datetime, "week_number": int, "day_type": str,
  "title": str, "description": str,
  "structure": {"warmup": str, "main_set": [{"reps": int, "distance_m": int, "effort_pct": int,
                "recovery": str, "note": str|None}], "finisher": str|None, "cues": [str], "total_volume_m": int},
  "target_distance_m": None, "pace_low_sec": None, "pace_high_sec": None, "hr_ceiling": None,
}
```
- 3 sessions/wk on `days` (Mon/Wed/Sat). Emphasis session weighted by `diagnosis` (speed_endurance -> heavier Sat).
- Phases: wk1 foundation incl. **baseline `test`** on day0; wk1..2 technique+accel (shorter if prior_speed_exposure);
  development wk3..N-2 (max_velocity + speed_endurance, ramp rep distance/volume); **deload every 4th** (~-40% volume,
  drop to 2 sessions); **taper** final week: technique+strides then final **`test`** (goal 100m) on the last day.
- Guardrails by construction: `days` spacing gives 48h between CNS-max; full prescribed recovery ("walk-back",
  work:rest by type); per-type volume caps ramped <=~15%/wk; plyo foot-contact cap ramped (~40->100); effort_pct
  capped (build to 100% max-velocity only after wk2). `total_volume_m` = sum(reps*distance_m) of main_set.
- `goal_date` = last workout's date. `goal_100m_sec` = target_100m_sec.

### coach_llm.py  (add)
```
generate_sprint_narrative(goal, profile, weeks_overview) -> {"overview": str, "weekly": [str]} | None
  goal = {"target_str": "17.7s", "current_str": "19.0s", "weeks": int}
  weeks_overview = [{"week": int, "phase": str, "focus": str}]
```
Grounded in profile (fade, diagnosis, 19s). Fail-safe: returns None on any error (same pattern as
generate_plan_narrative). Reuse `_chat` / `_extract_json`. max_tokens ~1800.

### sprint_tracking.py  (S2)
```
match_sprint_sessions(workouts, interval_activities, now, plan_start) -> {"workouts": enriched, "progress": progress}
  interval_activities = [{"id": int, "start_date": datetime, "best_100m_sec": float|None,
                          "fade_pct": float|None, "fastest_rep_sec": float|None}]
enriched wo adds: status (done|missed|upcoming|rest), actual ({activity_id, best_100m_sec, fade_pct, fastest_rep_sec}|None)
progress = {"sessions_done": int, "sessions_planned_past": int, "adherence_pct": int|None,
            "latest_best_100m_sec": float|None, "best_100m_trend": [{"date": str, "sec": float}]}
```
Match: workouts with day_type in {accel,max_velocity,speed_endurance,technique,test} matched to nearest unused
interval activity within ±2 days and >= plan_start; mark done. Runs from before plan_start ignored (as with 5K).

## Endpoints (main.py)
- `GET /api/plan/sprint/baseline` -> sprint profile (fetch best_efforts join activities for 100/200m,
  + Activity.interval_config where is_interval; call build_sprint_profile).
- `GET /api/plan/sprint/projections` -> uses baseline's best_100m_sec (400 if none) -> sprint_projections.
- `POST /api/plan`: extend `PlanCreateRequest` with `goal_type: str = "5k"`, optional `target_100m_sec: float|None`.
  Branch on goal_type: sprint -> build profile, generate_sprint_plan, generate_sprint_narrative, persist
  (goal_type, goal_distance_m=100, sprint_target_sec, target_time_sec=round(sprint_target_sec) for NOT-NULL, structure per wo).
- `_plan_response`: include `structure` per workout; include `sprint_target_sec` and profile (from fitness_snapshot);
  for sprint plans call `match_sprint_sessions` (S2) instead of 5K `match_and_grade`; goal-type-aware.
- `GET /api/plan/workout/{id}`: render `structure` for sprint; Actual from matched interval activity (S2).

## Frontend
- Create-plan page: **goal selector (5K | 100m Sprint)**. Sprint path: show auto baseline (19.0s) + supporting
  efforts + editable override; horizon cards w/ projected targets; diagnosis blurb; create.
- Training day cards: when `structure` present, render rep scheme ("6×30m accel · walk-back") + sprint type colors.
- PlanWorkoutDetail: render warmup / main-set table / finisher / cues; Actual card shows matched session best 100m +
  fade, links to existing interval-analysis screen.

## Testing
Unit tests per pure module (baseline diagnosis paths, projection math+reality, generator guardrails:
48h spacing / longest-emphasis / deload / taper-ends-in-test / effort ramp, structure volume). Endpoint tests
for sprint create + response shape + workout detail. Tracking match/miss/progress tests. All must pass alongside
the existing 46.
