"""Human/LLM-readable endpoint catalog.

Single source of truth for the in-app API page, GET /api/v1/catalog, and the
references/api.md bundled in the downloadable Claude skill.
"""
from __future__ import annotations

from . import settings as S


def conventions() -> list[str]:
    """Conventions text, reflecting this install's settings."""
    return [
        "Weights are in the user's display units (`units` field: lb or kg), as they log them. Dumbbells are "
        "per dumbbell. " + S.convention_text()
        + " See `GET /api/v1/settings`.",
        "Sessions logged with/without the bar contrary to the usual convention are corrected by a review "
        "decision (bar_included → bar subtracted, bar_excluded → bar added), so numbers are comparable. "
        "Look for the `adjustment` field.",
        *_STATIC_CONVENTIONS,
    ]


_STATIC_CONVENTIONS = [
    "Sessions flagged as suspicious jumps and not yet reviewed (`review_status: pending`) are left out "
    "of stats, trends and records until reviewed. Excluded sessions are always left out.",
    "`{exercise}` in a path accepts the exact Strong name or its slug: lowercase with non-alphanumerics "
    "turned into '-', e.g. `bench-press-barbell`. Slugs are listed in /api/v1/overview.",
    "e1rm = Epley estimated 1-rep max (reliable up to ~12 reps, rough above).",
    "Dates are YYYY-MM-DD. `as_of` lets you evaluate as if today were that date.",
    "Program percentages apply to the ACTUAL load. If barbell lifts are logged without the bar: "
    "weight = pct × (basis + bar) − bar, rounded to the program's rounding_lb / rounding_kg, and plans also return "
    "actual_load. If logged with the bar: weight = pct × basis.",
]

ENDPOINTS = [
    # ---- coach --------------------------------------------------------------
    {
        "group": "Coach", "method": "GET", "path": "/api/v1/overview",
        "summary": "Start here: the whole training picture in one call.",
        "use_for": "Finding exercise slugs, seeing which routines are current, how often you train, "
                   "and each exercise's current level and trend.",
        "params": [["as_of", "date", "Evaluate as of this date (default today)"]],
        "returns": [
            "history: first/last workout, total workouts, workouts in last 30/90 days",
            "routines_last_90d: workout names → count",
            "recent_workouts[]: last 10 workouts with their exercises",
            "pending_reviews: number of unreviewed flagged sessions",
            "active_improvement_plans[]: id, name, goal, exercises, current_phase/week (check these first)",
            "exercises[]: slug, sessions_total, last_date, days_since_last, sessions_last_30d/90d, "
            "current_top_weight, current_e1rm, best_e1rm, e1rm_trend_per_4wk_90d, pending_reviews",
        ],
        "example": "/api/v1/overview",
    },
    {
        "group": "Coach", "method": "GET", "path": "/api/v1/exercises/{exercise}/coach-context",
        "summary": "Everything needed to plan the next session for one exercise.",
        "use_for": "Suggesting next sets/reps/weight, detecting stalls, deciding when to add weight or deload.",
        "params": [["recent", "int 1–100", "How many recent sessions to include (default 12)"],
                   ["as_of", "date", "Evaluate as of this date"]],
        "returns": [
            "summary: same per-exercise stats as overview",
            "frequency: median days between sessions, sessions in last 30/90 days",
            "records: heaviest_set, best_e1rm, rep_maxes (best weight for 1+, 3+, 5+, 8+, 10+, 12+, 15+ reps)",
            "progression: all-time top weight and when first hit, sessions since matching it, current top, "
            "sessions/reps at current top, e1RM trend (per 4 weeks over 90 and 365 days), "
            "all_time_top_weight_milestones",
            "last_session: full set list",
            "recent_sessions[]: date, workout, compact sets string (e.g. '70x15, 120x8, 200x1'), "
            "top weight/reps, e1rm, volume",
            "data_quality: pending reviews for this exercise, adjusted/excluded counts",
            "how_to_read: plain-English explanation of the conventions",
            "active_improvement_plans[]: active plans that include this exercise",
        ],
        "example": "/api/v1/exercises/bench-press-barbell/coach-context?recent=8",
    },
    {
        "group": "Coach", "method": "GET", "path": "/api/v1/exercises/{exercise}/program-context",
        "summary": "Exercise history + the program you follow for it + adherence + next prescribed sessions.",
        "use_for": "Questions like 'I've been following UFPwrLifter, how do I break my bench plateau?'. Shows "
                   "whether you actually train as prescribed (load, reps, frequency), e1RM change since "
                   "starting, whether a re-test is due, and the next 3 prescribed sessions with weights.",
        "params": [["program", "slug", "Ad-hoc: compare against this program instead of saved assignments"],
                   ["since", "date", "Ad-hoc start date (default 8 weeks ago)"],
                   ["basis", "number or lift:number", "Ad-hoc 1RM/TM in display units (default: derived from e1RM)"],
                   ["recent", "int", "Recent sessions in coach_context (default 12)"],
                   ["as_of", "date", "Evaluate as of this date"]],
        "returns": [
            "coach_context: everything from /coach-context",
            "programs[]: assignment, program (summary, notes, one-line prescription per day), adherence",
            "adherence: weeks_on_program, sessions_logged, expected vs actual sessions/week, adherence_pct, "
            "verdict_counts (as_prescribed / lighter / heavier / fewer_reps), e1rm change since start, "
            "retest_due, sessions[] (prescribed vs actual per session), upcoming[] (next 3 sessions with weights)",
            "available_programs[] + note when nothing is assigned",
        ],
        "example": "/api/v1/exercises/bench-press-barbell/program-context?program=ufpwrlifter-bench&since=2026-09-01&recent=4",
    },
    # ---- data ---------------------------------------------------------------
    {
        "group": "Data", "method": "GET", "path": "/api/v1/exercises",
        "summary": "Every exercise with summary stats.",
        "use_for": "Listing what you do; `active_within_days` hides exercises you've dropped.",
        "params": [["active_within_days", "int", "Only exercises trained within N days"],
                   ["as_of", "date", "Evaluate as of this date"]],
        "returns": ["[] of the per-exercise summary objects (see overview.exercises)"],
        "example": "/api/v1/exercises?active_within_days=60",
    },
    {
        "group": "Data", "method": "GET", "path": "/api/v1/exercises/{exercise}/sessions",
        "summary": "Set-by-set history for one exercise, newest first.",
        "use_for": "Detailed inspection: exact sets, rep patterns, adjustments, review status.",
        "params": [["since", "date", "Only sessions on/after this date"],
                   ["limit", "int ≤2000", "Max sessions (default 50)"],
                   ["include_flagged", "bool", "Include pending/excluded sessions, marked (default true)"],
                   ["include_sets", "bool", "Include the set list (default true)"]],
        "returns": ["units; sessions[]: date, workout_name, top_weight, top_set_reps, e1rm, e1rm_from, "
                    "volume, total_reps, working_sets, sets[{set, weight, reps}], "
                    "review_status / adjustment / warning when relevant"],
        "example": "/api/v1/exercises/bench-press-barbell/sessions?limit=3",
    },
    {
        "group": "Data", "method": "GET", "path": "/api/v1/exercises/{exercise}/series",
        "summary": "Chart-ready time series, oldest first.",
        "use_for": "Plotting progress or computing your own trends.",
        "params": [["since", "date", "Only points on/after this date"]],
        "returns": ["units; points[]: date, top_weight, top_set_reps, e1rm, volume, total_reps, max_reps, "
                    "logged_top_weight (before adjustment), sets, status, reasons, bar_weight"],
        "example": "/api/v1/exercises/bench-press-barbell/series?since=2026-09-01",
    },
    {
        "group": "Data", "method": "GET", "path": "/api/v1/workouts",
        "summary": "Whole workouts, newest first, with every exercise and set.",
        "use_for": "Seeing what was trained recently (fatigue, overlap between days, routine structure).",
        "params": [["since", "date", "Only workouts on/after this date"],
                   ["limit", "int ≤1000", "Max workouts (default 20)"]],
        "returns": ["workouts[]: id, started_at, name, duration_min, notes, exercises[] (each with metrics and sets)"],
        "example": "/api/v1/workouts?limit=2",
    },
    {
        "group": "Data", "method": "GET", "path": "/api/v1/workouts/{workout_id}",
        "summary": "One workout by id.",
        "use_for": "Drilling into a workout from overview.recent_workouts.",
        "params": [],
        "returns": ["Same shape as one item of /api/v1/workouts"],
        "example": None,
    },
    {
        "group": "Data", "method": "GET", "path": "/api/v1/export/sets.csv",
        "summary": "Flat CSV of every set (normalized, display units).",
        "use_for": "Spreadsheets, pandas, or any tool that prefers CSV.",
        "params": [["exercise", "name/slug", "Only this exercise"],
                   ["include_flagged", "bool", "Include pending/excluded sessions (default false)"]],
        "returns": ["CSV columns: date, started_at, workout, exercise, set, weight_<unit>, reps, "
                    "logged_weight_<unit>, review_status"],
        "example": "/api/v1/export/sets.csv?exercise=bench-press-barbell",
    },
    # ---- programs -----------------------------------------------------------
    {
        "group": "Programs", "method": "GET", "path": "/api/v1/programs",
        "summary": "Program definitions (programs/*.json) with assignment counts.",
        "use_for": "Seeing which programs are available before planning or comparing.",
        "params": [],
        "returns": ["[]: slug, name, description, source, basis (1rm|training_max), weeks, days_per_week, "
                    "lifts, default_lift_mapping, min_weeks, progression, assignments"],
        "example": "/api/v1/programs",
    },
    {
        "group": "Programs", "method": "GET", "path": "/api/v1/programs/{slug}",
        "summary": "One program's full definition + a one-line prescription per day.",
        "use_for": "Reading exactly what a program prescribes.",
        "params": [],
        "returns": ["The program JSON (weeks → days → exercises → sets {sets, reps, reps_max, amrap, pct, "
                    "weight_lb | weight_kg, rpe, warmup}) plus prescription[] lines like '79%x5, 86%x3, 96%x1'"],
        "example": "/api/v1/programs/ufpwrlifter-bench",
    },
    {
        "group": "Programs", "method": "PUT", "path": "/api/v1/programs/{slug}",
        "summary": "Create or replace a program (writes programs/{slug}.json).",
        "use_for": "Adding a program from a description, spreadsheet or link. Percentages are of 1RM or "
                   "training max; each exercise has a `lift` key mapped to a Strong exercise via `lifts`.",
        "params": [["body", "JSON", "Program: slug, name, description, source, lifts {lift: exercise}, "
                                    "basis, training_max_pct, rounding_lb, rounding_kg, min_weeks, cycle_increment_lb, cycle_increment_kg, "
                                    "progression, notes, weeks[]"]],
        "returns": ["{saved, prescription[]}"], "example": None,
    },
    {
        "group": "Programs", "method": "DELETE", "path": "/api/v1/programs/{slug}",
        "summary": "Delete a program file (refused while assignments use it).",
        "use_for": "Cleanup.", "params": [], "returns": ["{deleted}"], "example": None,
    },
    {
        "group": "Programs", "method": "GET", "path": "/api/v1/programs/{slug}/plan",
        "summary": "Concrete weights for every program day.",
        "use_for": "Turning percentages into loads for your current max (or a max you pass).",
        "params": [["exercise", "slug or lift:slug,...", "Map lifts to Strong exercises (default: program mapping)"],
                   ["basis", "number or lift:number,...", "1RM/TM in display units (default: best e1RM of last 90 days)"],
                   ["cycle", "int", "Cycle number, applies the cycle increment (default 1)"],
                   ["as_of", "date", "Date for deriving the basis"]],
        "returns": ["units; basis per lift (value, how derived, exercise, bar), weeks → days → exercises → sets "
                    "{reps, pct, weight (as you log it), actual_load (incl. bar; only when you log "
                    "without the bar)}, compact strings"],
        "example": "/api/v1/programs/ufpwrlifter-bench/plan",
    },
    {
        "group": "Programs", "method": "GET", "path": "/api/v1/assignments",
        "summary": "Programs you follow / followed, for which exercises, since when.",
        "use_for": "Knowing which program applies to an exercise.",
        "params": [["active_only", "bool", "Only assignments active today"], ["as_of", "date", "Reference date"]],
        "returns": ["[]: id, program, exercise_map, basis (display units), start_date, end_date, notes"],
        "example": "/api/v1/assignments",
    },
    {
        "group": "Programs", "method": "POST", "path": "/api/v1/assignments",
        "summary": "Start following a program (start date may be in the past).",
        "use_for": "Recording 'I've been doing X on bench since March'.",
        "params": [["body", "JSON", "{program, start_date, exercise_map?, basis? (display units), end_date?, notes?}"]],
        "returns": ["The created assignment"], "example": None,
    },
    {
        "group": "Programs", "method": "PATCH", "path": "/api/v1/assignments/{aid}",
        "summary": "Update an assignment (end it, record a re-tested 1RM, fix dates).",
        "use_for": "Setting end_date when switching programs, or basis after a 1RM test.",
        "params": [["body", "JSON", "Any of {start_date, end_date, basis, exercise_map, notes}"]],
        "returns": ["The updated assignment"], "example": None,
    },
    {
        "group": "Programs", "method": "DELETE", "path": "/api/v1/assignments/{aid}",
        "summary": "Delete an assignment.", "use_for": "Removing a mistaken entry.",
        "params": [], "returns": ["{deleted}"], "example": None,
    },
    # ---- improvement plans ---------------------------------------------------
    {
        "group": "Improvement plans", "method": "GET", "path": "/api/v1/plans",
        "summary": "Your improvement plans: named, dated, personal plans (usually written by the coach skill).",
        "use_for": "Checking whether the user is already on a plan before recommending anything.",
        "params": [["status", "draft | active | completed | abandoned", "Filter (default all)"],
                   ["as_of", "date", "Reference date"]],
        "returns": ["[]: id, name, status, goal, exercises, start_date, end_date, current_phase, current_week, "
                    "author, updated_at"],
        "example": "/api/v1/plans?status=active",
    },
    {
        "group": "Improvement plans", "method": "POST", "path": "/api/v1/plans",
        "summary": "Create an improvement plan (stored in the local DB, not in git).",
        "use_for": "Saving a multi-week plan you recommended, after the user agrees. Sets reuse the program set "
                   "format: `pct` of the plan's max for that exercise, or absolute `weight_lb`/`weight_kg`, "
                   "or neither (reps only, e.g. bodyweight or 'pick a weight').",
        "params": [["body", "JSON",
                    "{name, status?: active|draft, goal, targets {exercise: max}, maxes {exercise: starting max}? "
                    "(display units, as logged; omitted = derived from best e1RM), diagnosis (markdown), "
                    "guardrails [str], notes, phases [{name, kind: deload|block|test|peak|other, start_date, "
                    "end_date, note?, progression?, weeks [{week, label?, days [{day, label?, note?, exercises "
                    "[{exercise, note?, sets [{sets, reps, reps_max?, pct? | weight_lb?, warmup?, amrap?, "
                    "note?}]}]}]}]}]}. A phase's last week template repeats if the phase is longer."]],
        "returns": ["The full plan (as GET /api/v1/plans/{pid})"], "example": None,
    },
    {
        "group": "Improvement plans", "method": "GET", "path": "/api/v1/plans/{pid}",
        "summary": "One plan in full, with concrete weights for every phase, week and day.",
        "use_for": "Reading back exactly what was prescribed and why.",
        "params": [["as_of", "date", "Reference date"]],
        "returns": ["summary fields + targets, diagnosis, guardrails, notes, maxes[] (date, exercise, value), "
                    "baseline {exercise: best_e1rm_90d, heaviest_single, rep_max_5, ...} at creation",
                    "phases[]: name, kind, dates, progression, weeks[].days[].exercises[] with sets "
                    "(weight as logged, actual_load, pct) and compact strings",
                    "definition: the raw plan JSON (for PATCH)"],
        "example": "/api/v1/plans/1",
    },
    {
        "group": "Improvement plans", "method": "GET", "path": "/api/v1/plans/{pid}/progress",
        "summary": "Where the user is in the plan and how it's going.",
        "use_for": "Check-ins: 'what's today's session?', 'am I on track?', 'should I adjust?'.",
        "params": [["as_of", "date", "Reference date"]],
        "returns": ["current: phase, week, this week's days with concrete weights; next_phase",
                    "exercises{}: sessions_logged, planned_in_completed_weeks, this_week {done, planned}, "
                    "verdict_counts, as_prescribed_pct, sessions[] (prescribed vs actual + verdict)",
                    "goals[]: target, baseline_e1rm_90d, best_e1rm_since_start, heaviest_since_start, "
                    "current_max, progress_pct",
                    "maxes[], guardrails[]"],
        "example": "/api/v1/plans/1/progress",
    },
    {
        "group": "Improvement plans", "method": "POST", "path": "/api/v1/plans/{pid}/maxes",
        "summary": "Record a new max (e.g. a test-day result); later prescriptions recalculate from it.",
        "use_for": "After a test day, or when the user re-tests.",
        "params": [["body", "JSON", "{exercise, value (display units, as logged), date, note?}"]],
        "returns": ["The full plan"], "example": None,
    },
    {
        "group": "Improvement plans", "method": "PATCH", "path": "/api/v1/plans/{pid}",
        "summary": "Edit a plan.",
        "use_for": "Changing status (completed / abandoned), adjusting phases, goal, guardrails or notes.",
        "params": [["body", "JSON", "Any of {name, status, goal, targets, diagnosis, guardrails, notes, phases}; "
                                    "phases replaces the whole list"]],
        "returns": ["The full plan"], "example": None,
    },
    {
        "group": "Improvement plans", "method": "DELETE", "path": "/api/v1/plans/{pid}",
        "summary": "Delete a plan.", "use_for": "Removing a mistaken plan (prefer status=abandoned to keep history).",
        "params": [], "returns": ["{deleted}"], "example": None,
    },
    # ---- review -------------------------------------------------------------
    {
        "group": "Review", "method": "GET", "path": "/api/v1/anomalies",
        "summary": "Sessions flagged as suspicious jumps.",
        "use_for": "Checking data quality before trusting a trend.",
        "params": [["status", "pending | reviewed | all", "Which flags to list (default pending)"],
                   ["exercise", "name/slug", "Only this exercise"]],
        "returns": ["units, default_bar_weight, count, items[]: started_at, exercise, status, reasons, "
                    "baseline (usual top/typical weight), logged sets, normalized top/e1rm, bar_weight"],
        "example": "/api/v1/anomalies?status=all&exercise=bench-press-barbell",
    },
    {
        "group": "Review", "method": "PUT", "path": "/api/v1/anomalies",
        "summary": "Record review decisions (one or many).",
        "use_for": "Marking flagged sessions OK, bar-corrected (logged with/without the bar contrary to "
                   "your usual convention), or excluded.",
        "params": [["body", "JSON array", "[{started_at, exercise, status: ok|bar_included|bar_excluded|"
                                          "exclude|pending, bar_weight (display units; default: settings bar weight), note}]"]],
        "returns": ["{updated: n}"],
        "example": None,
    },
    # ---- import -------------------------------------------------------------
    {
        "group": "Import", "method": "POST", "path": "/api/v1/import",
        "summary": "Upload a Strong CSV export; only the delta is applied.",
        "use_for": "Adding new workouts. New workouts are added, edited ones replaced, and a file already seen is skipped.",
        "params": [["file", "multipart", "The Strong export CSV"]],
        "returns": ["status, workouts_total, workouts_new, workouts_changed, workouts_missing, new_workout_dates"],
        "example": None,
    },
    {
        "group": "Import", "method": "POST", "path": "/api/v1/import/scan",
        "summary": "Import any new CSVs in the strong-extracts/ folder.",
        "use_for": "After dropping a new export into the folder.",
        "params": [], "returns": ["[] of import results"], "example": None,
    },
    {
        "group": "Import", "method": "GET", "path": "/api/v1/imports",
        "summary": "Import history.",
        "use_for": "Checking when data was last refreshed.",
        "params": [], "returns": ["[]: filename, imported_at, workouts_total/new/changed/missing"],
        "example": "/api/v1/imports",
    },
    # ---- settings -----------------------------------------------------------
    {
        "group": "Settings", "method": "GET", "path": "/api/v1/settings",
        "summary": "Your logging conventions.",
        "use_for": "Knowing whether logged barbell weights include the bar before interpreting or prescribing.",
        "params": [],
        "returns": ["units (lb | kg), bar_tracking (included | excluded), bar_weight (in units), convention (plain-English)"],
        "example": "/api/v1/settings",
    },
    {
        "group": "Settings", "method": "PUT", "path": "/api/v1/settings",
        "summary": "Save settings.",
        "use_for": "Changing the bar convention or bar weight (affects program weights and review defaults).",
        "params": [["body", "JSON", "{units: lb|kg, bar_tracking: included|excluded, bar_weight (in units)}"]],
        "returns": ["The saved settings"], "example": None,
    },
    # ---- meta ---------------------------------------------------------------
    {
        "group": "Meta", "method": "GET", "path": "/api/v1/catalog",
        "summary": "This endpoint catalog as JSON.",
        "use_for": "Letting an agent discover the API.",
        "params": [], "returns": ["conventions[], endpoints[]"], "example": "/api/v1/catalog",
    },
    {
        "group": "Meta", "method": "GET", "path": "/api/v1/skill.zip",
        "summary": "Download the Claude skill (strong-coach) as a zip.",
        "use_for": "Installing in Claude Code or uploading to Claude.",
        "params": [["base_url", "url", "API address baked into the skill (default: this server)"]],
        "returns": ["zip: strong-coach/SKILL.md, strong-coach/references/api.md"], "example": None,
    },
    {
        "group": "Meta", "method": "GET", "path": "/api/v1/skill.md",
        "summary": "The skill's SKILL.md as text.",
        "use_for": "Reading the skill instructions without downloading.",
        "params": [["base_url", "url", "API address baked into the skill (default: this server)"]],
        "returns": ["Markdown"], "example": "/api/v1/skill.md",
    },
]


def catalog() -> dict:
    return {"conventions": conventions(), "endpoints": ENDPOINTS}


def to_markdown(base_url: str) -> str:
    out = ["# Self Hosted Strong Sidecar API reference", "",
           "Unofficial; not affiliated with Strong or its developers.", "",
           f"Base URL: `{base_url}`", "", "## Conventions", ""]
    out += [f"- {c}" for c in conventions()]
    group = None
    for e in ENDPOINTS:
        if e["group"] != group:
            group = e["group"]
            out += ["", f"## {group}"]
        out += ["", f"### `{e['method']} {e['path']}`", "", e["summary"], "", f"Use for: {e['use_for']}"]
        if e["params"]:
            out += ["", "| Param | Type | Description |", "|---|---|---|"]
            out += [f"| `{n}` | {t} | {d} |" for n, t, d in e["params"]]
        out += ["", "Returns:"] + [f"- {r}" for r in e["returns"]]
        if e["example"]:
            out += ["", f"Example: `curl -s '{base_url}{e['example']}'`"]
    return "\n".join(out) + "\n"
