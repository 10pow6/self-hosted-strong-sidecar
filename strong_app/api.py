"""HTTP API + web UI.

Run:  .venv/bin/python -m strong_app serve      (http://127.0.0.1:8765, docs at /docs)
"""
from __future__ import annotations

import csv
import os
import zipfile
from contextlib import asynccontextmanager
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analysis as A
from . import plans as PL
from . import programs as P
from . import settings as S
from .catalog import catalog, to_markdown
from .db import ROOT, bump_version, connect
from .ingest import import_text, scan_folder

STATIC = Path(__file__).parent / "static"

conn = connect()
store = A.Store(conn)
S.load(conn)


@asynccontextmanager
async def lifespan(_app):
    scan_folder(conn)          # pick up any new exports dropped in strong-extracts/
    store.refresh()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Self Hosted Strong Sidecar API",
    version="1.0",
    description=(
        "Training history from Strong exports. All weights are in your display units (`units` in each "
        "response: lb or kg), as you log them, normalized for "
        "review decisions (sessions logged with/without the bar contrary to your usual convention are "
        "corrected; see `GET /api/v1/settings`). Start with "
        "`GET /api/v1/overview`, then `GET /api/v1/exercises/{slug}/coach-context` for a "
        "single exercise. Exercise path params accept the exact name or its slug "
        "(e.g. `bench-press-barbell`)."),
)


def _today(as_of: date | None) -> date:
    return as_of or date.today()


def _exercise(name: str) -> tuple[str, list[A.Session]]:
    store.refresh()
    ex = store.resolve(name)
    if not ex:
        raise HTTPException(404, f"Unknown exercise '{name}'. See /api/v1/exercises.")
    return ex, store.by_exercise[ex]


def _since(sessions, since: date | None):
    return [s for s in sessions if not since or s.day >= since]


# ---- overview / discovery --------------------------------------------------
@app.get("/api/v1/overview", tags=["coach"])
def overview(as_of: date | None = None):
    """Start here: date range, training frequency, every exercise with current level & trend."""
    store.refresh()
    today = _today(as_of)
    ws = sorted(store.workouts.values(), key=lambda w: w["started_at"])
    days = lambda w: (today - datetime.fromisoformat(w["started_at"]).date()).days
    names: dict[str, int] = {}
    for w in ws:
        if days(w) <= 90:
            names[w["name"]] = names.get(w["name"], 0) + 1
    return {
        "units": S.unit(), "convention": S.convention_text(), "as_of": today.isoformat(),
        "history": {"first_workout": ws[0]["started_at"][:10] if ws else None,
                    "last_workout": ws[-1]["started_at"][:10] if ws else None,
                    "workouts_total": len(ws),
                    "workouts_last_30d": sum(1 for w in ws if days(w) <= 30),
                    "workouts_last_90d": sum(1 for w in ws if days(w) <= 90)},
        "routines_last_90d": dict(sorted(names.items(), key=lambda kv: -kv[1])),
        "recent_workouts": [{"id": w["id"], "date": w["started_at"][:10], "name": w["name"],
                             "duration_min": w["duration_min"], "exercises": w["exercises"]}
                            for w in ws[-10:][::-1]],
        "pending_reviews": sum(1 for s in store.all_sessions() if s.status == "pending"),
        "active_improvement_plans": [PL.summary(PL.load(conn, i), store, today)
                                     for i in PL.list_ids(conn, "active")],
        "exercises": [A.exercise_summary(ex, ss, today) for ex, ss in store.by_exercise.items()],
    }


@app.get("/api/v1/exercises", tags=["data"])
def exercises(as_of: date | None = None, active_within_days: int | None = None):
    """All exercises with summary stats. `active_within_days=60` hides ones you no longer do."""
    store.refresh()
    today = _today(as_of)
    out = [A.exercise_summary(ex, ss, today) for ex, ss in store.by_exercise.items()]
    if active_within_days is not None:
        out = [e for e in out if e["days_since_last"] <= active_within_days]
    return out


@app.get("/api/v1/exercises/{exercise}/coach-context", tags=["coach"])
def coach_context(exercise: str, recent: int = Query(12, ge=1, le=100), as_of: date | None = None):
    """Compact, LLM-ready picture of one exercise: records, rep maxes, progression/stall
    signals, frequency, last session and the last N sessions. Use this to plan the next session."""
    ex, ss = _exercise(exercise)
    today = _today(as_of)
    return {**A.coach_context(ex, ss, today, recent),
            "active_improvement_plans": PL.active_for(conn, store, ex, today)}


@app.get("/api/v1/exercises/{exercise}/sessions", tags=["data"])
def exercise_sessions(exercise: str, since: date | None = None, limit: int = Query(50, ge=1, le=2000),
                      include_flagged: bool = True, include_sets: bool = True):
    """Per-session history (newest first) with every set. Flagged/excluded sessions are
    included but marked via `review_status` unless `include_flagged=false`."""
    ex, ss = _exercise(exercise)
    ss = _since(ss, since)
    if not include_flagged:
        ss = A.usable(ss)
    return {"exercise": ex, "units": S.unit(),
            "sessions": [A.session_dict(s, include_sets) for s in ss[::-1][:limit]]}


@app.get("/api/v1/exercises/{exercise}/series", tags=["data"])
def exercise_series(exercise: str, since: date | None = None):
    """Chart-ready time series (oldest first): top weight, e1RM, volume, reps per session,
    plus the as-logged top weight and review status for each point."""
    ex, ss = _exercise(exercise)
    pts = []
    for s in _since(ss, since):
        m, r = s.metrics, s.raw_metrics
        pts.append({"date": s.started_at[:10], "started_at": s.started_at, "workout": s.workout_name,
                    "top_weight": m.get("top_weight"), "top_set_reps": m.get("top_set_reps"),
                    "e1rm": m.get("e1rm"), "volume": m.get("volume"),
                    "total_reps": m.get("total_reps"), "max_reps": m.get("max_reps"),
                    "logged_top_weight": r.get("top_weight"),
                    "sets": ", ".join(f"{x.weight:g}x{x.reps}" for x in s.sets),
                    "status": s.status, "reasons": s.flag_reasons,
                    "bar_weight": s.bar_weight, "note": s.note})
    return {"exercise": ex, "units": S.unit(), "bodyweight": all(p["top_weight"] is None for p in pts),
            "points": pts}


@app.get("/api/v1/workouts", tags=["data"])
def workouts(since: date | None = None, limit: int = Query(20, ge=1, le=1000)):
    """Workouts newest first, each with its exercises and sets (normalized, display units)."""
    store.refresh()
    by_w: dict[int, list[A.Session]] = {}
    for s in store.all_sessions():
        by_w.setdefault(s.workout_id, []).append(s)
    ws = sorted(store.workouts.values(), key=lambda w: w["started_at"], reverse=True)
    if since:
        ws = [w for w in ws if w["started_at"][:10] >= since.isoformat()]
    out = []
    for w in ws[:limit]:
        order = {e: i for i, e in enumerate(w["exercises"])}
        sess = sorted(by_w.get(w["id"], []), key=lambda s: order.get(s.exercise, 0))
        out.append({**{k: w[k] for k in ("id", "started_at", "name", "duration_min", "notes")},
                    "exercises": [{"exercise": s.exercise, **{k: v for k, v in A.session_dict(s).items()
                                   if k not in ("date", "started_at", "workout_id", "workout_name")}}
                                  for s in sess]})
    return {"units": S.unit(), "workouts": out}


@app.get("/api/v1/workouts/{workout_id}", tags=["data"])
def workout(workout_id: int):
    res = [w for w in workouts(limit=1000)["workouts"] if w["id"] == workout_id]
    if not res:
        raise HTTPException(404, "No such workout")
    return res[0]


# ---- review / anomalies -------------------------------------------------------
@app.get("/api/v1/anomalies", tags=["review"])
def anomalies(status: Literal["pending", "reviewed", "all"] = "pending", exercise: str | None = None):
    """Sessions flagged as suspicious jumps (or already reviewed)."""
    store.refresh()
    ex = store.resolve(exercise) if exercise else None
    out = []
    for s in store.all_sessions():
        if ex and s.exercise != ex:
            continue
        flagged = bool(s.flag_reasons) or s.status in ("ok", "bar_included", "bar_excluded", "exclude")
        if not flagged:
            continue
        if status == "pending" and s.status != "pending":
            continue
        if status == "reviewed" and s.status == "pending":
            continue
        out.append(A.anomaly_dict(s))
    out.sort(key=lambda a: a["started_at"], reverse=True)
    return {"units": S.unit(), "default_bar_weight": S.bar_weight(), "bar_tracking": S.CURRENT.bar_tracking,
            "count": len(out), "items": out}


class Review(BaseModel):
    started_at: str = Field(..., description="Workout start timestamp, e.g. '2025-11-14 19:32:05'")
    exercise: str
    status: Literal["ok", "bar_included", "bar_excluded", "exclude", "pending"] = Field(
        ..., description="ok=real; bar_included=this session was logged INCLUDING the bar (subtract "
                         "bar_weight); bar_excluded=logged WITHOUT the bar (add bar_weight); "
                         "exclude=ignore; pending=clear the decision")
    bar_weight: float | None = Field(None, description="For bar_included/bar_excluded, in your display "
                                                       "units; default = settings bar weight")
    note: str | None = None


def _apply(r: Review):
    ex = store.resolve(r.exercise) or r.exercise
    if r.status == "pending":
        conn.execute("DELETE FROM annotations WHERE started_at=? AND exercise=?", (r.started_at, ex))
    else:
        # stored canonical lb
        bar = (S.from_disp(r.bar_weight) if r.bar_weight is not None else S.CURRENT.bar_weight_lb) \
            if r.status in A.BAR_ADJUST else None
        conn.execute(
            "INSERT INTO annotations(started_at, exercise, status, bar_weight_lb, note) VALUES (?,?,?,?,?) "
            "ON CONFLICT(started_at, exercise) DO UPDATE SET status=excluded.status, "
            "bar_weight_lb=excluded.bar_weight_lb, note=excluded.note, updated_at=datetime('now')",
            (r.started_at, ex, r.status, bar, r.note))


@app.put("/api/v1/anomalies", tags=["review"])
def review(items: list[Review]):
    """Record review decisions (one or many)."""
    for r in items:
        _apply(r)
    bump_version(conn)
    conn.commit()
    store.refresh()
    return {"updated": len(items)}


# ---- settings -----------------------------------------------------------------
@app.get("/api/v1/settings", tags=["settings"])
def get_settings():
    """Your display units and logging conventions (e.g. whether logged barbell weights include the bar)."""
    return S.to_api()


@app.put("/api/v1/settings", tags=["settings"])
def put_settings(s: S.SettingsIn):
    """Save settings (bar_weight is in the `units` you send). Affects every weight shown, program
    weight math and the default bar amount for reviews."""
    S.save(conn, S.from_api(s))
    bump_version(conn)
    conn.commit()
    store.refresh()
    return get_settings()


# ---- programs -----------------------------------------------------------------
def _pairs(v: str | None, prog: P.Program, numeric: bool = False) -> dict:
    """Parse 'bench:205,incline:60' (or a bare value for single-lift programs)."""
    if not v:
        return {}
    lifts = [k for k in P.program_summary(prog)["lifts"]]
    out = {}
    for part in v.split(","):
        k, sep, val = part.partition(":")
        if not sep:                      # bare value -> the program's first lift
            k, val = (lifts[0] if lifts else "main"), k
        out[k.strip()] = float(val) if numeric else val.strip()
    return out


def _program(slug: str) -> P.Program:
    try:
        prog = P.get_program(slug)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not prog:
        raise HTTPException(404, f"Unknown program '{slug}'. See /api/v1/programs.")
    return prog


@app.get("/api/v1/programs", tags=["programs"])
def programs():
    """Program definitions (from programs/*.json) with how many assignments use each."""
    used: dict[str, int] = {}
    for a in P.list_assignments(conn):
        used[a["program"]] = used.get(a["program"], 0) + 1
    return [{**P.program_summary(p), "assignments": used.get(p.slug, 0)} for p in P.list_programs()]


@app.get("/api/v1/programs/{slug}", tags=["programs"])
def program(slug: str):
    """Full definition plus a compact one-line-per-day prescription."""
    prog = _program(slug)
    return {**prog.model_dump(exclude_none=True), "prescription": P.prescription_text(prog)}


@app.put("/api/v1/programs/{slug}", tags=["programs"])
def put_program(slug: str, prog: P.Program):
    """Create or replace a program (writes programs/{slug}.json)."""
    if prog.slug != slug:
        raise HTTPException(400, "slug in body must match the URL")
    P.save_program(prog)
    return {"saved": slug, "prescription": P.prescription_text(prog)}


@app.delete("/api/v1/programs/{slug}", tags=["programs"])
def delete_program(slug: str):
    _program(slug)
    n = conn.execute("SELECT COUNT(*) FROM program_assignments WHERE program_slug=?", (slug,)).fetchone()[0]
    if n:
        raise HTTPException(409, f"{n} assignment(s) use this program; delete or end them first")
    P.delete_program(slug)
    return {"deleted": slug}


@app.get("/api/v1/programs/{slug}/plan", tags=["programs"])
def program_plan(slug: str, exercise: str | None = None, basis: str | None = None,
                 cycle: int = Query(1, ge=1), as_of: date | None = None):
    """Concrete weights for every day of the program. `exercise` / `basis` take
    'lift:value,...' or a bare value for single-lift programs (basis in your display units);
    basis defaults to your best e1RM (last 90 days), or the training max derived from it."""
    store.refresh()
    prog = _program(slug)
    return P.build_plan(store, prog, _pairs(exercise, prog), _pairs(basis, prog, numeric=True),
                        _today(as_of), cycle - 1)


class AssignmentIn(BaseModel):
    program: str
    start_date: date
    exercise_map: dict[str, str] = Field(default_factory=dict,
                                         description="lift -> Strong exercise (defaults to the program's mapping)")
    basis: dict[str, float] = Field(default_factory=dict,
                                    description="lift -> starting 1RM/TM as you log it, in display units; "
                                                "omit to derive from history")
    end_date: date | None = None
    notes: str | None = None


class AssignmentPatch(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    basis: dict[str, float] | None = None
    exercise_map: dict[str, str] | None = None
    notes: str | None = None


def _assignment(aid: int) -> dict:
    r = conn.execute("SELECT * FROM program_assignments WHERE id=?", (aid,)).fetchone()
    if not r:
        raise HTTPException(404, "No such assignment")
    return P._row_to_assignment(r)


@app.get("/api/v1/assignments", tags=["programs"])
def assignments(active_only: bool = False, as_of: date | None = None):
    """Which programs you're (or were) following, for which exercises, since when."""
    return P.list_assignments(conn, _today(as_of) if active_only else None)


@app.post("/api/v1/assignments", tags=["programs"])
def create_assignment(a: AssignmentIn):
    """Start following a program (start_date may be in the past)."""
    prog = _program(a.program)
    store.refresh()
    emap = {**prog.lifts, **a.exercise_map}
    bad = [f"{k}: {v}" for k, v in a.exercise_map.items() if not store.resolve(v)]
    if bad:
        raise HTTPException(400, f"Unknown exercise(s): {bad}")
    cur = conn.execute(
        "INSERT INTO program_assignments(program_slug, exercise_map, basis_lb, start_date, end_date, notes) "
        "VALUES (?,?,?,?,?,?)",
        (a.program, json.dumps(emap), P.basis_to_storage(a.basis),
         a.start_date.isoformat(), a.end_date.isoformat() if a.end_date else None, a.notes))
    conn.commit()
    return _assignment(cur.lastrowid)


@app.patch("/api/v1/assignments/{aid}", tags=["programs"])
def patch_assignment(aid: int, p: AssignmentPatch):
    """Update an assignment, e.g. set end_date when you stop, or record a re-tested 1RM in basis."""
    _assignment(aid)
    sets, vals = [], []
    for k, v in p.model_dump(exclude_unset=True).items():   # keys come from the model, not the client
        if k == "basis":
            sets.append("basis_lb=?")
            vals.append(P.basis_to_storage(v))
            continue
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if isinstance(v, dict) else v.isoformat() if isinstance(v, date) else v)
    if sets:
        conn.execute(f"UPDATE program_assignments SET {', '.join(sets)} WHERE id=?", (*vals, aid))
        conn.commit()
    return _assignment(aid)


@app.delete("/api/v1/assignments/{aid}", tags=["programs"])
def delete_assignment(aid: int):
    _assignment(aid)
    conn.execute("DELETE FROM program_assignments WHERE id=?", (aid,))
    conn.commit()
    return {"deleted": aid}


@app.get("/api/v1/exercises/{exercise}/program-context", tags=["coach"])
def program_context(exercise: str, program: str | None = None, since: date | None = None,
                    basis: str | None = None, recent: int = Query(12, ge=1, le=100),
                    as_of: date | None = None):
    """For questions like 'I've been following X, how do I break my plateau?': exercise history
    (coach-context) + the program(s) you follow for it + adherence (prescribed vs actual) + the
    next prescribed sessions. Uses active assignments, or an ad-hoc `program` + `since`."""
    ex, ss = _exercise(exercise)
    today = _today(as_of)
    out = {"exercise": ex, "as_of": today.isoformat(), "units": S.unit(),
           "coach_context": A.coach_context(ex, ss, today, recent), "programs": []}
    if program:
        prog = _program(program)
        maps_here = [l for l, e in prog.lifts.items() if store.resolve(e) == ex]
        emap = {**prog.lifts}
        if not maps_here:
            first = next((e.lift for w in prog.weeks for d in w.days for e in d.exercises), "main")
            emap[first] = ex
        targets = [{"id": None, "program": program, "exercise_map": emap,
                    "basis": _pairs(basis, prog, numeric=True),
                    "start_date": (since or today - timedelta(days=56)).isoformat(), "end_date": None,
                    "notes": "ad hoc (not saved)"}]
    else:
        targets = [a for a in P.list_assignments(conn, today)
                   if any(store.resolve(v) == ex for v in a["exercise_map"].values())]
    for a in targets:
        prog = P.get_program(a["program"])
        if not prog:
            continue
        out["programs"].append({
            "assignment": a,
            "program": {**P.program_summary(prog), "notes": prog.notes,
                        "prescription": P.prescription_text(prog)},
            "adherence": P.adherence(store, prog, a, ex, today),
        })
    if not out["programs"]:
        out["note"] = ("No program assigned to this exercise. Pass ?program=<slug>&since=YYYY-MM-DD for an "
                       "ad-hoc comparison, or create an assignment (POST /api/v1/assignments).")
        out["available_programs"] = [P.program_summary(p) for p in P.list_programs()]
    return out


# ---- improvement plans ---------------------------------------------------------
def _plan(pid: int) -> dict:
    p = PL.load(conn, pid)
    if not p:
        raise HTTPException(404, f"No improvement plan {pid}. See /api/v1/plans.")
    return p


@app.get("/api/v1/plans", tags=["plans"])
def plans(status: Literal["draft", "active", "completed", "abandoned"] | None = None, as_of: date | None = None):
    """Improvement plans (named, dated, personal plans, usually written by the coach skill)."""
    store.refresh()
    today = _today(as_of)
    return [PL.summary(PL.load(conn, i), store, today) for i in PL.list_ids(conn, status)]


@app.post("/api/v1/plans", tags=["plans"])
def create_plan(p: PL.PlanIn, as_of: date | None = None):
    """Create an improvement plan. Pct sets apply to the plan's max per exercise (from `maxes`,
    or derived from your best e1RM). A baseline snapshot is taken now."""
    store.refresh()
    today = _today(as_of)
    try:
        pid = PL.create(conn, store, p, today)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return PL.full(_plan(pid), store, today)


@app.get("/api/v1/plans/{pid}", tags=["plans"])
def get_plan(pid: int, as_of: date | None = None):
    """Full plan: goal, diagnosis, guardrails, maxes, baseline, and every phase/week/day with
    concrete weights (each week uses the max in effect when it starts)."""
    store.refresh()
    return PL.full(_plan(pid), store, _today(as_of))


@app.patch("/api/v1/plans/{pid}", tags=["plans"])
def patch_plan(pid: int, p: PL.PlanPatch, as_of: date | None = None):
    """Edit a plan: name, status, goal, targets, diagnosis, guardrails, notes, or replace phases."""
    _plan(pid)
    store.refresh()
    try:
        PL.patch(conn, store, pid, p)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return PL.full(_plan(pid), store, _today(as_of))


@app.delete("/api/v1/plans/{pid}", tags=["plans"])
def delete_plan(pid: int):
    _plan(pid)
    PL.delete(conn, pid)
    return {"deleted": pid}


@app.post("/api/v1/plans/{pid}/maxes", tags=["plans"])
def add_plan_max(pid: int, m: PL.MaxIn, as_of: date | None = None):
    """Record a new max (e.g. after a test day). Prescriptions from the day after use it."""
    _plan(pid)
    store.refresh()
    PL.add_max(conn, store, pid, m)
    return PL.full(_plan(pid), store, _today(as_of))


@app.get("/api/v1/plans/{pid}/progress", tags=["plans"])
def plan_progress(pid: int, as_of: date | None = None):
    """Where you are in the plan: current phase/week with this week's sessions (concrete weights),
    prescribed vs actual for every logged session, this week done/planned, and progress to goal."""
    store.refresh()
    return PL.progress(_plan(pid), store, _today(as_of))


# ---- import / export ----------------------------------------------------------
@app.post("/api/v1/import", tags=["import"])
async def upload(file: UploadFile = File(...)):
    """Upload a new Strong CSV export; only the delta is applied."""
    text = (await file.read()).decode("utf-8-sig")
    try:
        res = import_text(conn, text, file.filename or "upload.csv")
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.refresh()
    return res


@app.post("/api/v1/import/scan", tags=["import"])
def scan():
    """Import any new CSVs dropped into the strong-extracts/ folder."""
    res = scan_folder(conn)
    store.refresh()
    return res


@app.get("/api/v1/imports", tags=["import"])
def imports():
    return [dict(r) for r in conn.execute("SELECT * FROM imports ORDER BY id DESC")]


@app.get("/api/v1/export/sets.csv", tags=["data"], response_class=PlainTextResponse)
def export_sets(exercise: str | None = None, include_flagged: bool = False):
    """Flat CSV of every set (normalized, in your display units; the unit is in the column
    names) — easy to load into a spreadsheet or pandas."""
    store.refresh()
    ex = store.resolve(exercise) if exercise else None
    buf = io.StringIO()
    w = csv.writer(buf)
    u = S.unit()
    w.writerow(["date", "started_at", "workout", "exercise", "set", f"weight_{u}", "reps",
                f"logged_weight_{u}", "review_status"])
    rows = []
    for s in store.all_sessions():
        if (ex and s.exercise != ex) or (not include_flagged and s.status in ("pending", "exclude")):
            continue
        for x in s.sets:
            rows.append([s.started_at[:10], s.started_at, s.workout_name, s.exercise, x.set_order,
                         x.weight, x.reps, x.weight_raw, s.status or ""])
    rows.sort(key=lambda r: (r[1], r[3], r[4]))
    w.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


# ---- UI -------------------------------------------------------------------------
# ---- discovery & Claude skill ----------------------------------------------------
SKILL_DIR = ROOT / ".claude" / "skills" / "strong-coach"
DEFAULT_BASE = "http://127.0.0.1:8765"


def _base_url(request: Request, base_url: str | None) -> str:
    return (base_url or os.environ.get("STRONG_PUBLIC_URL") or str(request.base_url)).rstrip("/")


def _skill_md(base: str) -> str:
    # The repo copy is generic; the downloaded copy gets this install's address and path.
    return ((SKILL_DIR / "SKILL.md").read_text()
            .replace(DEFAULT_BASE, base)
            .replace("${STRONG_TRACKER_ROOT:-.}", str(ROOT)))


@app.get("/api/v1/catalog", tags=["meta"])
def get_catalog(request: Request):
    """Every endpoint with what it's for, its params and what it returns."""
    return {"base_url": _base_url(request, None), **catalog()}


@app.get("/api/v1/skill.zip", tags=["meta"])
def skill_zip(request: Request, base_url: str | None = None):
    """The strong-coach Claude skill (SKILL.md + references/api.md), pointed at this server."""
    base = _base_url(request, base_url)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("strong-coach/SKILL.md", _skill_md(base))
        z.writestr("strong-coach/references/api.md", to_markdown(base))
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="strong-coach.zip"'})


@app.get("/api/v1/skill.md", tags=["meta"], response_class=PlainTextResponse)
def skill_md(request: Request, base_url: str | None = None):
    """SKILL.md alone, for viewing."""
    return PlainTextResponse(_skill_md(_base_url(request, base_url)), media_type="text/markdown")


if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/", include_in_schema=False)
def index():
    if not (STATIC / "index.html").exists():
        return PlainTextResponse("UI not built yet: cd web && yarn install && yarn build "
                                 "(or use `yarn dev` on :5173). API docs at /docs.")
    return FileResponse(STATIC / "index.html")
