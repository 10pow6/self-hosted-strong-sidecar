# Self Hosted Strong Sidecar API reference

Unofficial; not affiliated with Strong or its developers.

Base URL: `http://127.0.0.1:8765`

## Conventions

- Weights are in the user's display units (`units` field: lb or kg), as they log them. Dumbbells are per dumbbell. Weights are in lb. Barbell lifts are logged as the total load including the bar. See `GET /api/v1/settings`.
- Sessions logged with/without the bar contrary to the usual convention are corrected by a review decision (bar_included → bar subtracted, bar_excluded → bar added), so numbers are comparable. Look for the `adjustment` field.
- Sessions flagged as suspicious jumps and not yet reviewed (`review_status: pending`) are left out of stats, trends and records until reviewed. Excluded sessions are always left out.
- `{exercise}` in a path accepts the exact Strong name or its slug: lowercase with non-alphanumerics turned into '-', e.g. `bench-press-barbell`. Slugs are listed in /api/v1/overview.
- e1rm = Epley estimated 1-rep max (reliable up to ~12 reps, rough above).
- Dates are YYYY-MM-DD. `as_of` lets you evaluate as if today were that date.
- Program percentages apply to the ACTUAL load. If barbell lifts are logged without the bar: weight = pct × (basis + bar) − bar, rounded to the program's rounding_lb / rounding_kg, and plans also return actual_load. If logged with the bar: weight = pct × basis.

## Coach

### `GET /api/v1/overview`

Start here: the whole training picture in one call.

Use for: Finding exercise slugs, seeing which routines are current, how often you train, and each exercise's current level and trend.

| Param | Type | Description |
|---|---|---|
| `as_of` | date | Evaluate as of this date (default today) |

Returns:
- history: first/last workout, total workouts, workouts in last 30/90 days
- routines_last_90d: workout names → count
- recent_workouts[]: last 10 workouts with their exercises
- pending_reviews: number of unreviewed flagged sessions
- active_improvement_plans[]: id, name, goal, exercises, current_phase/week (check these first)
- exercises[]: slug, sessions_total, last_date, days_since_last, sessions_last_30d/90d, current_top_weight, current_e1rm, best_e1rm, e1rm_trend_per_4wk_90d, pending_reviews

Example: `curl -s 'http://127.0.0.1:8765/api/v1/overview'`

### `GET /api/v1/exercises/{exercise}/coach-context`

Everything needed to plan the next session for one exercise.

Use for: Suggesting next sets/reps/weight, detecting stalls, deciding when to add weight or deload.

| Param | Type | Description |
|---|---|---|
| `recent` | int 1–100 | How many recent sessions to include (default 12) |
| `as_of` | date | Evaluate as of this date |

Returns:
- summary: same per-exercise stats as overview
- frequency: median days between sessions, sessions in last 30/90 days
- records: heaviest_set, best_e1rm, rep_maxes (best weight for 1+, 3+, 5+, 8+, 10+, 12+, 15+ reps)
- progression: all-time top weight and when first hit, sessions since matching it, current top, sessions/reps at current top, e1RM trend (per 4 weeks over 90 and 365 days), all_time_top_weight_milestones
- last_session: full set list
- recent_sessions[]: date, workout, compact sets string (e.g. '70x15, 120x8, 200x1'), top weight/reps, e1rm, volume
- data_quality: pending reviews for this exercise, adjusted/excluded counts
- how_to_read: plain-English explanation of the conventions
- active_improvement_plans[]: active plans that include this exercise

Example: `curl -s 'http://127.0.0.1:8765/api/v1/exercises/bench-press-barbell/coach-context?recent=8'`

### `GET /api/v1/exercises/{exercise}/program-context`

Exercise history + the program you follow for it + adherence + next prescribed sessions.

Use for: Questions like 'I've been following UFPwrLifter, how do I break my bench plateau?'. Shows whether you actually train as prescribed (load, reps, frequency), e1RM change since starting, whether a re-test is due, and the next 3 prescribed sessions with weights.

| Param | Type | Description |
|---|---|---|
| `program` | slug | Ad-hoc: compare against this program instead of saved assignments |
| `since` | date | Ad-hoc start date (default 8 weeks ago) |
| `basis` | number or lift:number | Ad-hoc 1RM/TM in display units (default: derived from e1RM) |
| `recent` | int | Recent sessions in coach_context (default 12) |
| `as_of` | date | Evaluate as of this date |

Returns:
- coach_context: everything from /coach-context
- programs[]: assignment, program (summary, notes, one-line prescription per day), adherence
- adherence: weeks_on_program, sessions_logged, expected vs actual sessions/week, adherence_pct, verdict_counts (as_prescribed / lighter / heavier / fewer_reps), e1rm change since start, retest_due, sessions[] (prescribed vs actual per session), upcoming[] (next 3 sessions with weights)
- available_programs[] + note when nothing is assigned

Example: `curl -s 'http://127.0.0.1:8765/api/v1/exercises/bench-press-barbell/program-context?program=ufpwrlifter-bench&since=2026-09-01&recent=4'`

## Data

### `GET /api/v1/exercises`

Every exercise with summary stats.

Use for: Listing what you do; `active_within_days` hides exercises you've dropped.

| Param | Type | Description |
|---|---|---|
| `active_within_days` | int | Only exercises trained within N days |
| `as_of` | date | Evaluate as of this date |

Returns:
- [] of the per-exercise summary objects (see overview.exercises)

Example: `curl -s 'http://127.0.0.1:8765/api/v1/exercises?active_within_days=60'`

### `GET /api/v1/exercises/{exercise}/sessions`

Set-by-set history for one exercise, newest first.

Use for: Detailed inspection: exact sets, rep patterns, adjustments, review status.

| Param | Type | Description |
|---|---|---|
| `since` | date | Only sessions on/after this date |
| `limit` | int ≤2000 | Max sessions (default 50) |
| `include_flagged` | bool | Include pending/excluded sessions, marked (default true) |
| `include_sets` | bool | Include the set list (default true) |

Returns:
- units; sessions[]: date, workout_name, top_weight, top_set_reps, e1rm, e1rm_from, volume, total_reps, working_sets, sets[{set, weight, reps}], review_status / adjustment / warning when relevant

Example: `curl -s 'http://127.0.0.1:8765/api/v1/exercises/bench-press-barbell/sessions?limit=3'`

### `GET /api/v1/exercises/{exercise}/series`

Chart-ready time series, oldest first.

Use for: Plotting progress or computing your own trends.

| Param | Type | Description |
|---|---|---|
| `since` | date | Only points on/after this date |

Returns:
- units; points[]: date, top_weight, top_set_reps, e1rm, volume, total_reps, max_reps, logged_top_weight (before adjustment), sets, status, reasons, bar_weight

Example: `curl -s 'http://127.0.0.1:8765/api/v1/exercises/bench-press-barbell/series?since=2026-09-01'`

### `GET /api/v1/workouts`

Whole workouts, newest first, with every exercise and set.

Use for: Seeing what was trained recently (fatigue, overlap between days, routine structure).

| Param | Type | Description |
|---|---|---|
| `since` | date | Only workouts on/after this date |
| `limit` | int ≤1000 | Max workouts (default 20) |

Returns:
- workouts[]: id, started_at, name, duration_min, notes, exercises[] (each with metrics and sets)

Example: `curl -s 'http://127.0.0.1:8765/api/v1/workouts?limit=2'`

### `GET /api/v1/workouts/{workout_id}`

One workout by id.

Use for: Drilling into a workout from overview.recent_workouts.

Returns:
- Same shape as one item of /api/v1/workouts

### `GET /api/v1/export/sets.csv`

Flat CSV of every set (normalized, display units).

Use for: Spreadsheets, pandas, or any tool that prefers CSV.

| Param | Type | Description |
|---|---|---|
| `exercise` | name/slug | Only this exercise |
| `include_flagged` | bool | Include pending/excluded sessions (default false) |

Returns:
- CSV columns: date, started_at, workout, exercise, set, weight_<unit>, reps, logged_weight_<unit>, review_status

Example: `curl -s 'http://127.0.0.1:8765/api/v1/export/sets.csv?exercise=bench-press-barbell'`

## Programs

### `GET /api/v1/programs`

Program definitions (programs/*.json) with assignment counts.

Use for: Seeing which programs are available before planning or comparing.

Returns:
- []: slug, name, description, source, basis (1rm|training_max), weeks, days_per_week, lifts, default_lift_mapping, min_weeks, progression, assignments

Example: `curl -s 'http://127.0.0.1:8765/api/v1/programs'`

### `GET /api/v1/programs/{slug}`

One program's full definition + a one-line prescription per day.

Use for: Reading exactly what a program prescribes.

Returns:
- The program JSON (weeks → days → exercises → sets {sets, reps, reps_max, amrap, pct, weight_lb | weight_kg, rpe, warmup}) plus prescription[] lines like '79%x5, 86%x3, 96%x1'

Example: `curl -s 'http://127.0.0.1:8765/api/v1/programs/ufpwrlifter-bench'`

### `PUT /api/v1/programs/{slug}`

Create or replace a program (writes programs/{slug}.json).

Use for: Adding a program from a description, spreadsheet or link. Percentages are of 1RM or training max; each exercise has a `lift` key mapped to a Strong exercise via `lifts`.

| Param | Type | Description |
|---|---|---|
| `body` | JSON | Program: slug, name, description, source, lifts {lift: exercise}, basis, training_max_pct, rounding_lb, rounding_kg, min_weeks, cycle_increment_lb, cycle_increment_kg, progression, notes, weeks[] |

Returns:
- {saved, prescription[]}

### `DELETE /api/v1/programs/{slug}`

Delete a program file (refused while assignments use it).

Use for: Cleanup.

Returns:
- {deleted}

### `GET /api/v1/programs/{slug}/plan`

Concrete weights for every program day.

Use for: Turning percentages into loads for your current max (or a max you pass).

| Param | Type | Description |
|---|---|---|
| `exercise` | slug or lift:slug,... | Map lifts to Strong exercises (default: program mapping) |
| `basis` | number or lift:number,... | 1RM/TM in display units (default: best e1RM of last 90 days) |
| `cycle` | int | Cycle number, applies the cycle increment (default 1) |
| `as_of` | date | Date for deriving the basis |

Returns:
- units; basis per lift (value, how derived, exercise, bar), weeks → days → exercises → sets {reps, pct, weight (as you log it), actual_load (incl. bar; only when you log without the bar)}, compact strings

Example: `curl -s 'http://127.0.0.1:8765/api/v1/programs/ufpwrlifter-bench/plan'`

### `GET /api/v1/assignments`

Programs you follow / followed, for which exercises, since when.

Use for: Knowing which program applies to an exercise.

| Param | Type | Description |
|---|---|---|
| `active_only` | bool | Only assignments active today |
| `as_of` | date | Reference date |

Returns:
- []: id, program, exercise_map, basis (display units), start_date, end_date, notes

Example: `curl -s 'http://127.0.0.1:8765/api/v1/assignments'`

### `POST /api/v1/assignments`

Start following a program (start date may be in the past).

Use for: Recording 'I've been doing X on bench since March'.

| Param | Type | Description |
|---|---|---|
| `body` | JSON | {program, start_date, exercise_map?, basis? (display units), end_date?, notes?} |

Returns:
- The created assignment

### `PATCH /api/v1/assignments/{aid}`

Update an assignment (end it, record a re-tested 1RM, fix dates).

Use for: Setting end_date when switching programs, or basis after a 1RM test.

| Param | Type | Description |
|---|---|---|
| `body` | JSON | Any of {start_date, end_date, basis, exercise_map, notes} |

Returns:
- The updated assignment

### `DELETE /api/v1/assignments/{aid}`

Delete an assignment.

Use for: Removing a mistaken entry.

Returns:
- {deleted}

## Improvement plans

### `GET /api/v1/plans`

Your improvement plans: named, dated, personal plans (usually written by the coach skill).

Use for: Checking whether the user is already on a plan before recommending anything.

| Param | Type | Description |
|---|---|---|
| `status` | draft | active | completed | abandoned | Filter (default all) |
| `as_of` | date | Reference date |

Returns:
- []: id, name, status, goal, exercises, start_date, end_date, current_phase, current_week, author, updated_at

Example: `curl -s 'http://127.0.0.1:8765/api/v1/plans?status=active'`

### `POST /api/v1/plans`

Create an improvement plan (stored in the local DB, not in git).

Use for: Saving a multi-week plan you recommended, after the user agrees. Sets reuse the program set format: `pct` of the plan's max for that exercise, or absolute `weight_lb`/`weight_kg`, or neither (reps only, e.g. bodyweight or 'pick a weight').

| Param | Type | Description |
|---|---|---|
| `body` | JSON | {name, status?: active|draft, goal, targets {exercise: max}, maxes {exercise: starting max}? (display units, as logged; omitted = derived from best e1RM), diagnosis (markdown), guardrails [str], notes, phases [{name, kind: deload|block|test|peak|other, start_date, end_date, note?, progression?, weeks [{week, label?, days [{day, label?, note?, exercises [{exercise, note?, sets [{sets, reps, reps_max?, pct? | weight_lb?, warmup?, amrap?, note?}]}]}]}]}]}. A phase's last week template repeats if the phase is longer. |

Returns:
- The full plan (as GET /api/v1/plans/{pid})

### `GET /api/v1/plans/{pid}`

One plan in full, with concrete weights for every phase, week and day.

Use for: Reading back exactly what was prescribed and why.

| Param | Type | Description |
|---|---|---|
| `as_of` | date | Reference date |

Returns:
- summary fields + targets, diagnosis, guardrails, notes, maxes[] (date, exercise, value), baseline {exercise: best_e1rm_90d, heaviest_single, rep_max_5, ...} at creation
- phases[]: name, kind, dates, progression, weeks[].days[].exercises[] with sets (weight as logged, actual_load, pct) and compact strings
- definition: the raw plan JSON (for PATCH)

Example: `curl -s 'http://127.0.0.1:8765/api/v1/plans/1'`

### `GET /api/v1/plans/{pid}/progress`

Where the user is in the plan and how it's going.

Use for: Check-ins: 'what's today's session?', 'am I on track?', 'should I adjust?'.

| Param | Type | Description |
|---|---|---|
| `as_of` | date | Reference date |

Returns:
- current: phase, week, this week's days with concrete weights; next_phase
- exercises{}: sessions_logged, planned_in_completed_weeks, this_week {done, planned}, verdict_counts, as_prescribed_pct, sessions[] (prescribed vs actual + verdict)
- goals[]: target, baseline_e1rm_90d, best_e1rm_since_start, heaviest_since_start, current_max, progress_pct
- maxes[], guardrails[]

Example: `curl -s 'http://127.0.0.1:8765/api/v1/plans/1/progress'`

### `POST /api/v1/plans/{pid}/maxes`

Record a new max (e.g. a test-day result); later prescriptions recalculate from it.

Use for: After a test day, or when the user re-tests.

| Param | Type | Description |
|---|---|---|
| `body` | JSON | {exercise, value (display units, as logged), date, note?} |

Returns:
- The full plan

### `PATCH /api/v1/plans/{pid}`

Edit a plan.

Use for: Changing status (completed / abandoned), adjusting phases, goal, guardrails or notes.

| Param | Type | Description |
|---|---|---|
| `body` | JSON | Any of {name, status, goal, targets, diagnosis, guardrails, notes, phases}; phases replaces the whole list |

Returns:
- The full plan

### `DELETE /api/v1/plans/{pid}`

Delete a plan.

Use for: Removing a mistaken plan (prefer status=abandoned to keep history).

Returns:
- {deleted}

## Review

### `GET /api/v1/anomalies`

Sessions flagged as suspicious jumps.

Use for: Checking data quality before trusting a trend.

| Param | Type | Description |
|---|---|---|
| `status` | pending | reviewed | all | Which flags to list (default pending) |
| `exercise` | name/slug | Only this exercise |

Returns:
- units, default_bar_weight, count, items[]: started_at, exercise, status, reasons, baseline (usual top/typical weight), logged sets, normalized top/e1rm, bar_weight

Example: `curl -s 'http://127.0.0.1:8765/api/v1/anomalies?status=all&exercise=bench-press-barbell'`

### `PUT /api/v1/anomalies`

Record review decisions (one or many).

Use for: Marking flagged sessions OK, bar-corrected (logged with/without the bar contrary to your usual convention), or excluded.

| Param | Type | Description |
|---|---|---|
| `body` | JSON array | [{started_at, exercise, status: ok|bar_included|bar_excluded|exclude|pending, bar_weight (display units; default: settings bar weight), note}] |

Returns:
- {updated: n}

## Import

### `POST /api/v1/import`

Upload a Strong CSV export; only the delta is applied.

Use for: Adding new workouts. New workouts are added, edited ones replaced, and a file already seen is skipped.

| Param | Type | Description |
|---|---|---|
| `file` | multipart | The Strong export CSV |

Returns:
- status, workouts_total, workouts_new, workouts_changed, workouts_missing, new_workout_dates

### `POST /api/v1/import/scan`

Import any new CSVs in the strong-extracts/ folder.

Use for: After dropping a new export into the folder.

Returns:
- [] of import results

### `GET /api/v1/imports`

Import history.

Use for: Checking when data was last refreshed.

Returns:
- []: filename, imported_at, workouts_total/new/changed/missing

Example: `curl -s 'http://127.0.0.1:8765/api/v1/imports'`

## Settings

### `GET /api/v1/settings`

Your logging conventions.

Use for: Knowing whether logged barbell weights include the bar before interpreting or prescribing.

Returns:
- units (lb | kg), bar_tracking (included | excluded), bar_weight (in units), convention (plain-English)

Example: `curl -s 'http://127.0.0.1:8765/api/v1/settings'`

### `PUT /api/v1/settings`

Save settings.

Use for: Changing the bar convention or bar weight (affects program weights and review defaults).

| Param | Type | Description |
|---|---|---|
| `body` | JSON | {units: lb|kg, bar_tracking: included|excluded, bar_weight (in units)} |

Returns:
- The saved settings

## Meta

### `GET /api/v1/catalog`

This endpoint catalog as JSON.

Use for: Letting an agent discover the API.

Returns:
- conventions[], endpoints[]

Example: `curl -s 'http://127.0.0.1:8765/api/v1/catalog'`

### `GET /api/v1/skill.zip`

Download the Claude skill (strong-coach) as a zip.

Use for: Installing in Claude Code or uploading to Claude.

| Param | Type | Description |
|---|---|---|
| `base_url` | url | API address baked into the skill (default: this server) |

Returns:
- zip: strong-coach/SKILL.md, strong-coach/references/api.md

### `GET /api/v1/skill.md`

The skill's SKILL.md as text.

Use for: Reading the skill instructions without downloading.

| Param | Type | Description |
|---|---|---|
| `base_url` | url | API address baked into the skill (default: this server) |

Returns:
- Markdown

Example: `curl -s 'http://127.0.0.1:8765/api/v1/skill.md'`
