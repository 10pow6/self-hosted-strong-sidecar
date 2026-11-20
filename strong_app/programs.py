"""Training programs: definitions (JSON files in programs/), assignments (DB), concrete plans,
and adherence of logged sessions against the prescription.

Weight math respects the user's bar setting (settings.py). Program percentages apply to the
ACTUAL load, so with offset = bar weight if the user logs barbell lifts without the bar, else 0:
    logged = round(pct * (basis_logged + offset) - offset)
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import analysis as A
from . import settings as S
from .db import PROGRAMS_DIR

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


# ---- definition schema ------------------------------------------------------------
class SetSpec(BaseModel):
    sets: int = Field(1, ge=1, description="How many sets of this line")
    reps: int | None = Field(None, ge=0, description="Reps per set (minimum if reps_max given)")
    reps_max: int | None = Field(None, description="Upper end of a rep range, e.g. 12-15")
    amrap: bool = Field(False, description="As many reps as possible (reps = minimum)")
    pct: float | None = Field(None, gt=0, le=150, description="% of the basis (1RM or training max)")
    weight_lb: float | None = Field(None, description="Absolute weight in lb instead of pct (as you log it)")
    weight_kg: float | None = Field(None, description="Absolute weight in kg instead of pct (as you log it)")
    rpe: float | None = None
    warmup: bool = False
    note: str | None = None


class ExerciseSpec(BaseModel):
    lift: str = Field(..., description="Program lift key, e.g. 'bench'; mapped to a Strong exercise")
    sets: list[SetSpec]
    note: str | None = None


class DaySpec(BaseModel):
    day: int
    label: str | None = None
    exercises: list[ExerciseSpec]
    note: str | None = None


class WeekSpec(BaseModel):
    week: int
    label: str | None = None
    days: list[DaySpec]


class Program(BaseModel):
    slug: str
    name: str
    description: str = ""
    source: str | None = None
    lifts: dict[str, str] = Field(default_factory=dict,
                                  description="Default mapping lift key -> Strong exercise name/slug")
    basis: Literal["1rm", "training_max"] = "1rm"
    training_max_pct: float = Field(100, description="When basis is derived from e1RM: TM = e1RM * this %")
    rounding_lb: float = Field(5, gt=0, description="Round prescribed weights to this step when using lb")
    rounding_kg: float = Field(2.5, gt=0, description="Round prescribed weights to this step when using kg")
    min_weeks: int | None = Field(None, description="Run at least this many weeks before re-testing")
    cycle_increment_lb: dict[str, float] = Field(default_factory=dict,
                                                 description="Basis increase per completed cycle, per lift (lb)")
    cycle_increment_kg: dict[str, float] = Field(default_factory=dict,
                                                 description="Same in kg; used for kg users if given, "
                                                             "otherwise the lb values are converted")
    progression: str = ""
    notes: str = ""
    weeks: list[WeekSpec]

    @field_validator("slug")
    @classmethod
    def _slug(cls, v):
        if not SLUG_RE.match(v):
            raise ValueError("slug must be lowercase letters, digits and '-' (max 64)")
        return v


# ---- file storage -------------------------------------------------------------------
def _path(slug: str):
    if not SLUG_RE.match(slug):
        raise ValueError("invalid program slug")
    return PROGRAMS_DIR / f"{slug}.json"


def list_programs() -> list[Program]:
    PROGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(PROGRAMS_DIR.glob("*.json")):
        try:
            out.append(Program.model_validate_json(p.read_text()))
        except Exception:      # a malformed file shouldn't break the API; it's reported by get
            continue
    return out


def get_program(slug: str) -> Program | None:
    p = _path(slug)
    return Program.model_validate_json(p.read_text()) if p.exists() else None


def save_program(prog: Program) -> None:
    PROGRAMS_DIR.mkdir(parents=True, exist_ok=True)
    _path(prog.slug).write_text(json.dumps(prog.model_dump(exclude_none=True), indent=2) + "\n")


def delete_program(slug: str) -> bool:
    p = _path(slug)
    if p.exists():
        p.unlink()
        return True
    return False


def program_summary(p: Program) -> dict:
    n_days = sum(len(w.days) for w in p.weeks)
    order = list(dict.fromkeys(e.lift for w in p.weeks for d in w.days for e in d.exercises))
    return {"slug": p.slug, "name": p.name, "description": p.description, "source": p.source,
            "basis": p.basis, "weeks": len(p.weeks), "days_per_week": round(n_days / max(len(p.weeks), 1), 1),
            "lifts": order, "primary_lift": order[0] if order else None,
            "default_lift_mapping": p.lifts, "min_weeks": p.min_weeks, "progression": p.progression}


def prescription_text(p: Program) -> list[str]:
    """One compact line per program day, e.g. 'W1 D1 Monday | bench: 33%x10(wu), 79%x5, ...'."""
    def one(s: SetSpec) -> str:
        load = f"{s.pct:g}%" if s.pct is not None else (f"{s.weight_lb:g}lb" if s.weight_lb is not None else "BW")
        reps = f"{s.reps}-{s.reps_max}" if s.reps_max else f"{s.reps}+" if s.amrap else f"{s.reps}"
        txt = f"{load}x{reps}" + ("(wu)" if s.warmup else "") + (f"@RPE{s.rpe:g}" if s.rpe else "")
        return f"{s.sets}x[{txt}]" if s.sets > 1 else txt
    out = []
    for w in p.weeks:
        for d in w.days:
            ex = "; ".join(f"{e.lift}: " + ", ".join(one(s) for s in e.sets) for e in d.exercises)
            out.append(f"W{w.week}{' ' + w.label if w.label else ''} D{d.day}{' ' + d.label if d.label else ''} | {ex}")
    return out


# ---- assignments --------------------------------------------------------------------
def _row_to_assignment(r: sqlite3.Row) -> dict:
    # basis is stored canonical lb; exposed in display units
    stored = json.loads(r["basis_lb"]) if r["basis_lb"] else {}
    return {"id": r["id"], "program": r["program_slug"], "exercise_map": json.loads(r["exercise_map"]),
            "basis": {k: S.to_disp(v) for k, v in stored.items()},
            "start_date": r["start_date"], "end_date": r["end_date"], "notes": r["notes"]}


def basis_to_storage(basis: dict[str, float] | None) -> str | None:
    """Display-unit basis -> canonical-lb JSON for the DB."""
    return json.dumps({k: round(S.from_disp(v), 3) for k, v in basis.items()}) if basis else None


def list_assignments(conn, active_on: date | None = None) -> list[dict]:
    rows = [_row_to_assignment(r) for r in
            conn.execute("SELECT * FROM program_assignments ORDER BY start_date DESC, id DESC")]
    if active_on:
        d = active_on.isoformat()
        rows = [a for a in rows if a["start_date"] <= d and (not a["end_date"] or a["end_date"] >= d)]
    return rows


# ---- weight math --------------------------------------------------------------------
def bar_for(exercise: str | None) -> float:
    """lb to add to a logged weight of this exercise to get the actual load."""
    return S.bar_offset() if exercise and "(barbell)" in exercise.lower() else 0.0


def _round(x: float, step: float) -> float:
    return round(round(x / step) * step, 2)


# ---- unit handling: program files carry lb (and optional kg) values; output is display units
def step_for(prog: Program) -> float:
    return prog.rounding_kg if S.unit() == "kg" else prog.rounding_lb


def cycle_inc(prog: Program, lift: str) -> float:
    if S.unit() == "kg":
        if lift in prog.cycle_increment_kg:
            return prog.cycle_increment_kg[lift]
        return S.to_disp(prog.cycle_increment_lb.get(lift, 0)) or 0
    if lift in prog.cycle_increment_lb:
        return prog.cycle_increment_lb[lift]
    return S.rnd(prog.cycle_increment_kg.get(lift, 0) / S.KG_PER_LB)


def abs_weight(spec: SetSpec) -> float | None:
    if S.unit() == "kg":
        return spec.weight_kg if spec.weight_kg is not None else S.to_disp(spec.weight_lb)
    if spec.weight_lb is not None:
        return spec.weight_lb
    return S.rnd(spec.weight_kg / S.KG_PER_LB) if spec.weight_kg is not None else None


def weight_for(spec: SetSpec, basis_logged: float | None, bar: float, step: float) -> float | None:
    w = abs_weight(spec)
    if w is not None:
        return w
    if spec.pct is None or basis_logged is None:
        return None
    return max(_round(spec.pct / 100 * (basis_logged + bar) - bar, step), 0.0)


def resolve_basis(store: A.Store, prog: Program, lift: str, exercise: str | None,
                  explicit: float | None, ref: date) -> dict:
    """Number the percentages apply to (as the user logs it), with an explanation."""
    if explicit is not None:
        return {"value": explicit, "how": f"set explicitly ({prog.basis})"}
    if not exercise or exercise not in store.by_exercise:
        return {"value": None, "how": "no exercise mapped / no data; set the 1RM/TM"}
    sess = [s for s in A.usable(store.by_exercise[exercise])
            if s.day <= ref and not s.metrics.get("bodyweight") and s.metrics.get("e1rm")]
    recent = [s for s in sess if (ref - s.day).days <= 90] or sess
    if not recent:
        return {"value": None, "how": "no weighted history; set the 1RM/TM"}
    best = max(recent, key=lambda s: s.metrics["e1rm"])
    e1 = best.metrics["e1rm"]
    val, how = e1, (f"best e1RM {e1:g} {S.unit()} ({best.metrics['e1rm_from']} on {best.started_at[:10]}) "
                    f"in the 90 days before {ref}")
    if prog.basis == "training_max":
        val = S.rnd(e1 * prog.training_max_pct / 100)
        how += f"; training max = {prog.training_max_pct:g}% of that"
    return {"value": val, "how": how}


def concrete_sets(ex: ExerciseSpec, basis: float | None, bar: float, step: float) -> list[dict]:
    out = []
    for s in ex.sets:
        w = weight_for(s, basis, bar, step)
        reps = (f"{s.reps}-{s.reps_max}" if s.reps_max else f"{s.reps}+" if s.amrap else s.reps)
        for _ in range(s.sets):
            d = {"reps": reps, "weight": w}
            if s.pct is not None:
                d["pct"] = s.pct
            if w is not None and bar:
                d["actual_load"] = S.rnd(w + bar)
            for k in ("rpe", "note"):
                if getattr(s, k) is not None:
                    d[k] = getattr(s, k)
            if s.warmup:
                d["warmup"] = True
            out.append(d)
    return out


def fmt_sets(sets: list[dict]) -> str:
    return ", ".join(f"{d['weight']:g}x{d['reps']}" if d.get("weight") is not None else f"BWx{d['reps']}"
                     for d in sets)


def build_plan(store: A.Store, prog: Program, exercise_map: dict[str, str], basis: dict[str, float],
               ref: date, cycle: int = 0) -> dict:
    lifts = sorted({e.lift for w in prog.weeks for d in w.days for e in d.exercises})
    mapping = {l: store.resolve(exercise_map.get(l) or prog.lifts.get(l) or "") for l in lifts}
    pct_lifts = {e.lift for w in prog.weeks for d in w.days for e in d.exercises
                 if any(s.pct is not None for s in e.sets)}
    bases = {}
    for l in lifts:
        if l not in pct_lifts:
            bases[l] = {"value": None, "how": "not percentage-based", "exercise": mapping[l],
                        "bar": bar_for(mapping[l])}
            continue
        b = resolve_basis(store, prog, l, mapping[l], basis.get(l), ref)
        inc = cycle_inc(prog, l) * cycle
        if b["value"] is not None and inc:
            b = {"value": S.rnd(b["value"] + inc),
                 "how": b["how"] + f"; +{inc:g} {S.unit()} for cycle {cycle + 1}"}
        bases[l] = {**b, "exercise": mapping[l], "bar": bar_for(mapping[l])}
    weeks = []
    for w in prog.weeks:
        days = []
        for d in w.days:
            exs = []
            for e in d.exercises:
                b = bases[e.lift]
                sets = concrete_sets(e, b["value"], b["bar"], step_for(prog))
                exs.append({"lift": e.lift, "exercise": b["exercise"], "note": e.note,
                            "sets": sets, "compact": fmt_sets(sets)})
            days.append({"week": w.week, "day": d.day, "label": d.label, "note": d.note, "exercises": exs})
        weeks.append({"week": w.week, "label": w.label, "days": days})
    return {"program": program_summary(prog), "basis": bases, "cycle": cycle + 1,
            "units": S.unit(),
            "convention": "weight is as you log it. " + S.convention_text()
                          + (" actual_load includes the bar." if S.bar_offset() else ""),
            "weeks": weeks}


# ---- adherence ----------------------------------------------------------------------
def _schedule_for(prog: Program, lift_keys: set[str]):
    return [(w, d, e) for w in prog.weeks for d in w.days for e in d.exercises if e.lift in lift_keys]


def _compare(presc: list[dict], s: A.Session, step: float) -> dict:
    work = [p for p in presc if not p.get("warmup") and p.get("weight")]
    if not work:
        return {"verdict": "not_comparable"}
    p_top = max(p["weight"] for p in work)
    p_min = min(p["weight"] for p in work)
    p_reps = sum(int(str(p["reps"]).split("-")[0].rstrip("+")) for p in work)
    act = [x for x in s.sets if x.weight > 0 and (x.reps or 0) > 0]
    # Logged warm-ups aren't flagged, so skip the leading sets lighter than the first
    # prescribed work set, at most as many as the warm-ups prescribed before it.
    first_work = next(i for i, p in enumerate(presc) if not p.get("warmup") and p.get("weight"))
    n_warm = sum(1 for p in presc[:first_work] if p.get("warmup"))
    skip = 0
    while skip < min(n_warm, len(act)) and act[skip].weight < presc[first_work]["weight"] - step:
        skip += 1
    a_work = [x for x in act[skip:] if x.weight >= p_min - step]
    a_top = max((x.weight for x in act), default=0)
    a_reps = sum(x.reps for x in a_work)
    ratio = a_top / p_top if p_top else 0
    if abs(ratio - 1) <= 0.04 and a_reps >= 0.85 * p_reps:
        v = "as_prescribed"
    elif ratio < 0.96:
        v = "lighter"
    elif ratio > 1.04:
        v = "heavier"
    else:
        v = "fewer_reps"
    return {"verdict": v, "prescribed_top": p_top, "actual_top": a_top,
            "top_vs_prescribed_pct": round(ratio * 100, 1),
            "prescribed_working_sets": len(work), "actual_working_sets": len(a_work),
            "prescribed_working_reps": p_reps, "actual_working_reps": a_reps}


def adherence(store: A.Store, prog: Program, assignment: dict, exercise: str, today: date) -> dict:
    """Compare every logged session of `exercise` since the start with the prescribed sequence."""
    lifts = {l for l, ex in assignment["exercise_map"].items() if store.resolve(ex) == exercise}
    lifts |= {l for l, ex in prog.lifts.items() if store.resolve(ex) == exercise
              and l not in assignment["exercise_map"]}
    sched = _schedule_for(prog, lifts)
    if not sched:
        return {"error": f"program has no lift mapped to {exercise}"}
    start = date.fromisoformat(assignment["start_date"])
    end = date.fromisoformat(assignment["end_date"]) if assignment.get("end_date") else today
    sess = [s for s in A.usable(store.by_exercise.get(exercise, [])) if start <= s.day <= end]
    lift = next(iter(lifts))
    explicit = assignment.get("basis", {}).get(lift)
    rows, counts = [], {}
    for k, s in enumerate(sess):
        w, d, e = sched[k % len(sched)]
        cyc = k // len(sched)
        # Explicit basis: fixed (+ per-cycle increments). Otherwise rolling: best e1RM in the
        # 90 days before this session, approximating periodic re-tests.
        b = resolve_basis(store, prog, lift, exercise, explicit, s.day - timedelta(days=1))["value"]
        if b is not None and explicit is not None:
            b += cycle_inc(prog, lift) * cyc
        presc = concrete_sets(e, b, bar_for(exercise), step_for(prog))
        cmp_ = _compare(presc, s, step_for(prog))
        counts[cmp_["verdict"]] = counts.get(cmp_["verdict"], 0) + 1
        rows.append({"date": s.started_at[:10], "expected": f"cycle {cyc + 1} week {w.week} day {d.day}"
                     + (f" ({d.label})" if d.label else ""),
                     "prescribed": fmt_sets(presc), "actual": ", ".join(f"{x.weight:g}x{x.reps}" for x in s.sets),
                     **cmp_})
    k = len(sess)
    upcoming = []
    for i in range(3):
        w, d, e = sched[(k + i) % len(sched)]
        cyc = (k + i) // len(sched)
        plan_basis = resolve_basis(store, prog, lift, exercise, explicit, today)
        b = plan_basis["value"]
        if b is not None and explicit is not None:
            b += cycle_inc(prog, lift) * cyc
        sets = concrete_sets(e, b, bar_for(exercise), step_for(prog))
        upcoming.append({"position": f"cycle {cyc + 1} week {w.week} day {d.day}" + (f" ({d.label})" if d.label else ""),
                         "basis": b, "basis_how": plan_basis["how"], "sets": sets, "compact": fmt_sets(sets)})
    weighted = [s for s in sess if s.metrics.get("e1rm")]
    before = [s for s in A.usable(store.by_exercise.get(exercise, []))
              if start - timedelta(days=30) <= s.day < start and s.metrics.get("e1rm")]
    e1_start = max((s.metrics["e1rm"] for s in before), default=None)
    e1_now = max((s.metrics["e1rm"] for s in weighted[-3:]), default=None)
    e1_best = max((s.metrics["e1rm"] for s in weighted), default=None)
    weeks_in = (min(end, today) - start).days / 7
    test_due = bool(prog.min_weeks and weeks_in >= prog.min_weeks)
    return {
        "exercise": exercise, "lifts": sorted(lifts),
        "start_date": assignment["start_date"], "weeks_on_program": round(weeks_in, 1),
        "sessions_logged": k, "expected_sessions_per_week": round(len(sched) / max(len(prog.weeks), 1), 1),
        "actual_sessions_per_week": round(k / weeks_in, 1) if weeks_in >= 1 else None,
        "adherence_pct": round(100 * counts.get("as_prescribed", 0) / k) if k else None,
        "verdict_counts": counts,
        "e1rm": {"best_in_30d_before_start": e1_start, "best_since_start": e1_best,
                 "recent_best_last_3_sessions": e1_now,
                 "change_since_start": S.rnd(e1_best - e1_start) if e1_best and e1_start else None},
        "retest_due": test_due,
        "retest_rule": prog.progression or None,
        "matching_note": ("Sequential: the Nth session after start_date is compared with the Nth prescribed "
                          "day for this lift (warm-ups ignored). Skipped/extra days shift the alignment."),
        "sessions": rows[-12:],
        "upcoming": upcoming,
    }
