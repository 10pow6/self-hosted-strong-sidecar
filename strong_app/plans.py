"""Improvement plans: named, dated, personal training plans (usually written by the strong-coach
skill) that sit above programs. A plan has a goal, a diagnosis, guardrails and phases (deload,
block, test, ...); each phase has week templates of days with concrete prescriptions.

Unlike programs (generic templates in git-tracked programs/*.json), plans are personal and live
in the SQLite DB under data/. Set specs reuse the program SetSpec (pct of the plan's max, or an
absolute weight), so the same bar-aware weight math applies. Maxes are recorded over time (e.g.
after a test day); every prescription uses the max in effect at that date.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from . import analysis as A
from . import programs as P
from . import settings as S

PhaseKind = Literal["deload", "block", "test", "peak", "other"]

# ---- definition schema ------------------------------------------------------------
class PlanExercise(BaseModel):
    exercise: str = Field(..., description="Strong exercise name or slug, e.g. 'Bench Press (Barbell)'")
    sets: list[P.SetSpec]
    note: str | None = None


class PlanDay(BaseModel):
    day: int
    label: str | None = None
    note: str | None = None
    exercises: list[PlanExercise]


class PlanWeek(BaseModel):
    week: int
    label: str | None = None
    days: list[PlanDay]


class Phase(BaseModel):
    name: str
    kind: PhaseKind = "block"
    start_date: date
    end_date: date
    note: str | None = None
    progression: str | None = Field(None, description="How load/reps advance within the phase")
    weeks: list[PlanWeek] = Field(..., description="Week templates; the last one repeats if the phase is longer")

    @model_validator(mode="after")
    def _dates(self):
        if self.end_date < self.start_date:
            raise ValueError(f"phase '{self.name}': end_date before start_date")
        if not self.weeks:
            raise ValueError(f"phase '{self.name}': needs at least one week")
        return self


class PlanBody(BaseModel):
    """The editable content of a plan (everything except id/status/maxes/baseline)."""
    goal: str = Field("", description="One line, e.g. 'Bench 1RM 100 → 110 kg'")
    targets: dict[str, float] = Field(default_factory=dict,
                                      description="exercise -> target max (display units, as logged)")
    diagnosis: str = Field("", description="Why this plan: what the data showed (markdown)")
    guardrails: list[str] = Field(default_factory=list, description="Rules for adjusting on the fly")
    notes: str = ""
    rounding_lb: float = Field(5, gt=0)
    rounding_kg: float = Field(2.5, gt=0)
    phases: list[Phase]

    @model_validator(mode="after")
    def _order(self):
        if not self.phases:
            raise ValueError("a plan needs at least one phase")
        ph = sorted(self.phases, key=lambda p: p.start_date)
        for a, b in zip(ph, ph[1:]):
            if b.start_date <= a.end_date:
                raise ValueError(f"phases '{a.name}' and '{b.name}' overlap")
        self.phases = ph
        return self


class PlanIn(PlanBody):
    name: str = Field(..., min_length=1, max_length=120)
    status: Literal["draft", "active", "completed", "abandoned"] = "active"
    author: str | None = Field("strong-coach skill", description="Who wrote it")
    maxes: dict[str, float] = Field(default_factory=dict,
                                    description="exercise -> starting max (display units, as logged) that "
                                                "pct sets apply to; omitted ones are derived from history")


class PlanPatch(BaseModel):
    name: str | None = None
    status: Literal["draft", "active", "completed", "abandoned"] | None = None
    goal: str | None = None
    targets: dict[str, float] | None = None
    diagnosis: str | None = None
    guardrails: list[str] | None = None
    notes: str | None = None
    phases: list[Phase] | None = None


class MaxIn(BaseModel):
    exercise: str
    value: float = Field(..., gt=0, description="New max in display units, as logged")
    date: date
    note: str | None = None


# ---- helpers ------------------------------------------------------------------------
def _resolve(store: A.Store, name: str) -> str:
    """Canonical Strong name when known; otherwise keep what was given (not logged yet)."""
    return store.resolve(name) or name


def _all_exercises(body: PlanBody) -> list[str]:
    return list(dict.fromkeys(e.exercise for ph in body.phases for w in ph.weeks
                              for d in w.days for e in d.exercises))


def _pct_exercises(body: PlanBody) -> set[str]:
    return {e.exercise for ph in body.phases for w in ph.weeks for d in w.days for e in d.exercises
            if any(s.pct is not None for s in e.sets)}


def canonicalize(store: A.Store, body: PlanBody) -> PlanBody:
    """Map every exercise name/slug in the plan to its Strong name."""
    for ph in body.phases:
        for w in ph.weeks:
            for d in w.days:
                for e in d.exercises:
                    e.exercise = _resolve(store, e.exercise)
    body.targets = {_resolve(store, k): v for k, v in body.targets.items()}
    return body


def step(body: PlanBody) -> float:
    return body.rounding_kg if S.unit() == "kg" else body.rounding_lb


def _snapshot(store: A.Store, ex: str, today: date) -> dict:
    """Baseline numbers (canonical lb) for an exercise at plan creation."""
    ss = store.by_exercise.get(ex)
    if not ss:
        return {"note": "no history"}
    c = A.coach_context(ex, ss, today, 1)
    r, p, s = c.get("records") or {}, c.get("progression") or {}, c.get("summary") or {}
    lb = lambda v: S.from_disp(v) if v is not None else None
    use = [x for x in A.usable(ss) if (today - x.day).days <= 90 and x.metrics.get("e1rm")]
    return {"as_of": today.isoformat(),
            "best_e1rm_90d": lb(max((x.metrics["e1rm"] for x in use), default=None)),
            "best_e1rm_all_time": lb((r.get("best_e1rm") or {}).get("e1rm")),
            "heaviest_single": lb((r.get("heaviest_set") or {}).get("weight")),
            "rep_max_5": lb(((r.get("rep_maxes") or {}).get("5+_reps") or {}).get("weight")),
            "current_top_weight": lb(p.get("current_top_weight")),
            "sessions_last_30d": s.get("sessions_last_30d")}


def _disp_snapshot(snap: dict) -> dict:
    return {k: (S.to_disp(v) if isinstance(v, (int, float)) and k not in ("sessions_last_30d",) else v)
            for k, v in snap.items()}


# ---- storage ------------------------------------------------------------------------
def create(conn, store: A.Store, p: PlanIn, today: date) -> int:
    body = canonicalize(store, PlanBody(**p.model_dump(include=set(PlanBody.model_fields))))
    start, end = body.phases[0].start_date, body.phases[-1].end_date
    maxes = []
    for ex in sorted(_pct_exercises(body)):
        given = next((v for k, v in p.maxes.items() if _resolve(store, k) == ex), None)
        if given is None:
            b = P.resolve_basis(store, P.Program(slug="x", name="x", weeks=[]), "x", ex, None, start)
            if b["value"] is None:
                raise ValueError(f"no history to derive a max for '{ex}'; pass maxes")
            given, note = b["value"], "derived: " + b["how"]
        else:
            note = "starting max"
        maxes.append({"date": start.isoformat(), "exercise": ex, "value_lb": S.from_disp(given), "note": note})
    baseline = {ex: _snapshot(store, ex, today) for ex in _all_exercises(body) if ex in store.by_exercise}
    cur = conn.execute(
        "INSERT INTO improvement_plans(name, status, start_date, end_date, body, maxes, baseline, author) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (p.name, p.status, start.isoformat(), end.isoformat(), body.model_dump_json(),
         json.dumps(maxes), json.dumps(baseline), p.author))
    conn.commit()
    return cur.lastrowid


def _row(conn, pid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM improvement_plans WHERE id=?", (pid,)).fetchone()


def load(conn, pid: int) -> dict | None:
    r = _row(conn, pid)
    if not r:
        return None
    return {"id": r["id"], "name": r["name"], "status": r["status"], "start_date": r["start_date"],
            "end_date": r["end_date"], "body": PlanBody.model_validate_json(r["body"]),
            "maxes": json.loads(r["maxes"]), "baseline": json.loads(r["baseline"]),
            "author": r["author"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def list_ids(conn, status: str | None = None) -> list[int]:
    q = "SELECT id FROM improvement_plans" + (" WHERE status=?" if status else "") + \
        " ORDER BY (status='active') DESC, start_date DESC, id DESC"
    return [r[0] for r in conn.execute(q, (status,) if status else ())]


def patch(conn, store: A.Store, pid: int, p: PlanPatch) -> None:
    cur = load(conn, pid)
    data = p.model_dump(exclude_unset=True)
    body = cur["body"].model_dump()
    for k in ("goal", "targets", "diagnosis", "guardrails", "notes", "phases"):
        if k in data:
            body[k] = data[k]
    new = canonicalize(store, PlanBody.model_validate(body))
    conn.execute(
        "UPDATE improvement_plans SET name=?, status=?, body=?, start_date=?, end_date=?, "
        "updated_at=datetime('now') WHERE id=?",
        (data.get("name") or cur["name"], data.get("status") or cur["status"], new.model_dump_json(),
         new.phases[0].start_date.isoformat(), new.phases[-1].end_date.isoformat(), pid))
    conn.commit()


def add_max(conn, store: A.Store, pid: int, m: MaxIn) -> None:
    cur = load(conn, pid)
    maxes = cur["maxes"] + [{"date": m.date.isoformat(), "exercise": _resolve(store, m.exercise),
                             "value_lb": S.from_disp(m.value), "note": m.note or "recorded max"}]
    maxes.sort(key=lambda x: x["date"])
    conn.execute("UPDATE improvement_plans SET maxes=?, updated_at=datetime('now') WHERE id=?",
                 (json.dumps(maxes), pid))
    conn.commit()


def delete(conn, pid: int) -> None:
    conn.execute("DELETE FROM improvement_plans WHERE id=?", (pid,))
    conn.commit()


# ---- weight math / rendering ----------------------------------------------------------
def max_on(plan: dict, ex: str, d: date) -> float | None:
    """Max (display units) in effect for prescriptions on day d: the latest recorded strictly
    before d, else the starting one. A max recorded on a test day applies from the next day."""
    rows = [m for m in plan["maxes"] if m["exercise"] == ex]
    if not rows:
        return None
    before = [m for m in rows if m["date"] < d.isoformat()]
    return S.to_disp((before or rows[:1])[-1]["value_lb"])


def _sets(e: PlanExercise, basis: float | None, stp: float) -> list[dict]:
    return P.concrete_sets(e, basis, P.bar_for(e.exercise), stp)


def compact(sets: list[dict]) -> str:
    out = []
    for d in sets:
        w = d.get("weight")
        out.append(f"{w:g}x{d['reps']}" if w is not None else f"{d['reps']} reps")
    return ", ".join(out)


def phase_at(body: PlanBody, d: date) -> tuple[int, Phase] | tuple[None, None]:
    for i, ph in enumerate(body.phases):
        if ph.start_date <= d <= ph.end_date:
            return i, ph
    return None, None


def week_index(ph: Phase, d: date) -> int:
    return min((d - ph.start_date).days // 7, len(ph.weeks) - 1)


def render_day(plan: dict, day: PlanDay, on: date) -> dict:
    stp = step(plan["body"])
    exs = []
    for e in day.exercises:
        b = max_on(plan, e.exercise, on)
        sets = _sets(e, b, stp)
        exs.append({"exercise": e.exercise, "note": e.note, "max_used": b if any(s.pct for s in e.sets) else None,
                    "bar": P.bar_for(e.exercise), "sets": sets, "compact": compact(sets)})
    return {"day": day.day, "label": day.label, "note": day.note, "exercises": exs}


def render_phases(plan: dict) -> list[dict]:
    """Every phase/week/day with concrete weights, using the max in effect at each week's start."""
    out = []
    for ph in plan["body"].phases:
        n_weeks = max(len(ph.weeks), ((ph.end_date - ph.start_date).days // 7) + 1)
        weeks = []
        for wi, w in enumerate(ph.weeks):
            wstart = ph.start_date + timedelta(days=7 * wi)
            weeks.append({"week": w.week, "label": w.label, "starts": wstart.isoformat(),
                          "days": [render_day(plan, d, wstart) for d in w.days]})
        out.append({"name": ph.name, "kind": ph.kind, "start_date": ph.start_date.isoformat(),
                    "end_date": ph.end_date.isoformat(), "note": ph.note, "progression": ph.progression,
                    "calendar_weeks": n_weeks, "weeks": weeks})
    return out


# ---- progress / adherence --------------------------------------------------------------
def _match(plan: dict, store: A.Store, ex: str, today: date) -> list[dict]:
    """Each logged session of ex in the plan window, matched to the prescribed day: the Nth session
    of ex within a plan week is compared with the Nth day of that week that includes ex."""
    body, stp = plan["body"], step(plan["body"])
    start, end = date.fromisoformat(plan["start_date"]), min(date.fromisoformat(plan["end_date"]), today)
    seen: dict[tuple[int, int], int] = {}
    rows = []
    for s in A.usable(store.by_exercise.get(ex, [])):
        if not (start <= s.day <= end):
            continue
        pi, ph = phase_at(body, s.day)
        actual = ", ".join(f"{x.weight:g}x{x.reps}" for x in s.sets)
        base = {"date": s.started_at[:10], "workout": s.workout_name, "actual": actual}
        if ph is None:
            rows.append({**base, "phase": None, "verdict": "outside_phases"})
            continue
        wi = week_index(ph, s.day)
        days = [d for d in ph.weeks[wi].days if any(e.exercise == ex for e in d.exercises)]
        k = seen.get((pi, wi), 0)
        seen[(pi, wi)] = k + 1
        where = {"phase": ph.name, "week": ph.weeks[wi].week}
        if k >= len(days):
            rows.append({**base, **where, "day": None, "verdict": "extra"})
            continue
        d = days[k]
        e = next(e for e in d.exercises if e.exercise == ex)
        presc = _sets(e, max_on(plan, ex, s.day), stp)
        cmp_ = P._compare(presc, s, stp) if any(p.get("weight") for p in presc) else {"verdict": "logged"}
        # Plan weights are already rounded to the plate step, so (unlike programs' ±4% tolerance)
        # any set a full step above the prescribed top is a real deviation, e.g. 200 for a 195 single.
        over = [x for x in s.sets if cmp_.get("prescribed_top") and x.weight >= cmp_["prescribed_top"] + stp]
        if over and cmp_["verdict"] in ("as_prescribed", "fewer_reps"):
            cmp_ = {**cmp_, "verdict": "heavier", "sets_above_prescribed_top": len(over)}
        rows.append({**base, **where, "day": d.label or f"Day {d.day}", "prescribed": compact(presc), **cmp_})
    return rows


def _planned_until(body: PlanBody, ex: str, today: date) -> int:
    """Sessions of ex prescribed in weeks that have fully ended by today."""
    n = 0
    for ph in body.phases:
        wk = ph.start_date
        while wk <= ph.end_date:
            wend = min(wk + timedelta(days=6), ph.end_date)
            if wend < today:
                w = ph.weeks[week_index(ph, wk)]
                n += sum(1 for d in w.days if any(e.exercise == ex for e in d.exercises))
            wk += timedelta(days=7)
    return n


def progress(plan: dict, store: A.Store, today: date) -> dict:
    body = plan["body"]
    start, end = date.fromisoformat(plan["start_date"]), date.fromisoformat(plan["end_date"])
    pi, ph = phase_at(body, today)
    current = None
    if ph is not None:
        wi = week_index(ph, today)
        wstart = ph.start_date + timedelta(days=7 * ((today - ph.start_date).days // 7))
        current = {"phase_index": pi, "phase": ph.name, "kind": ph.kind, "note": ph.note,
                   "progression": ph.progression,
                   "week": ph.weeks[wi].week, "week_label": ph.weeks[wi].label,
                   "weeks_in_phase": len(ph.weeks), "week_starts": wstart.isoformat(),
                   "phase_ends": ph.end_date.isoformat(),
                   "days": [render_day(plan, d, today) for d in ph.weeks[wi].days]}
    nxt = next((p for p in body.phases if p.start_date > today), None)
    per_ex = {}
    for ex in _all_exercises(body):
        rows = _match(plan, store, ex, today)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        week_rows = []
        if current:
            ws = date.fromisoformat(current["week_starts"])
            week_rows = [r for r in rows if ws.isoformat() <= r["date"] <= (ws + timedelta(days=6)).isoformat()]
        planned_week = sum(1 for d in (current or {}).get("days", []) if any(e["exercise"] == ex for e in d["exercises"]))
        compared = sum(v for k, v in counts.items() if k in ("as_prescribed", "lighter", "heavier", "fewer_reps"))
        per_ex[ex] = {"sessions_logged": len(rows), "planned_in_completed_weeks": _planned_until(body, ex, today),
                      "this_week": {"done": len(week_rows), "planned": planned_week},
                      "verdict_counts": counts,
                      "as_prescribed_pct": round(100 * counts.get("as_prescribed", 0) / compared) if compared else None,
                      "sessions": rows[-15:]}
    goals = []
    for ex, target in body.targets.items():
        use = [s for s in A.usable(store.by_exercise.get(ex, [])) if start <= s.day <= today and s.metrics.get("e1rm")]
        best = max(use, key=lambda s: s.metrics["e1rm"], default=None)
        heaviest = max(((x.weight, s.started_at[:10]) for s in use for x in s.sets if (x.reps or 0) >= 1),
                       default=None)
        snap = _disp_snapshot(plan["baseline"].get(ex, {}))
        from_ = snap.get("best_e1rm_90d")
        latest_max = max_on(plan, ex, today + timedelta(days=1))
        goals.append({"exercise": ex, "target": target, "baseline_e1rm_90d": from_,
                      "best_e1rm_since_start": best.metrics["e1rm"] if best else None,
                      "best_e1rm_from": f"{best.metrics['e1rm_from']} on {best.started_at[:10]}" if best else None,
                      "heaviest_since_start": {"weight": heaviest[0], "date": heaviest[1]} if heaviest else None,
                      "current_max": latest_max,
                      "progress_pct": (round(100 * ((latest_max or 0) - from_) / (target - from_))
                                       if from_ is not None and latest_max is not None and target != from_ else None)})
    return {"id": plan["id"], "name": plan["name"], "status": plan["status"], "as_of": today.isoformat(),
            "units": S.unit(), "convention": S.convention_text(),
            "start_date": plan["start_date"], "end_date": plan["end_date"],
            "days_in": (today - start).days, "days_total": (end - start).days + 1,
            "goal": body.goal, "goals": goals, "current": current,
            "next_phase": {"name": nxt.name, "kind": nxt.kind, "starts": nxt.start_date.isoformat()} if nxt else None,
            "guardrails": body.guardrails,
            "maxes": [{**{k: v for k, v in m.items() if k != "value_lb"}, "value": S.to_disp(m["value_lb"])}
                      for m in plan["maxes"]],
            "exercises": per_ex,
            "matching_note": ("Within each plan week, the Nth logged session of an exercise is compared with the "
                              "Nth day of that week that includes it (warm-ups ignored). Sessions beyond the "
                              "planned days are 'extra'.")}


# ---- API shapes ---------------------------------------------------------------------------
def summary(plan: dict, store: A.Store, today: date) -> dict:
    body = plan["body"]
    _, ph = phase_at(body, today)
    return {"id": plan["id"], "name": plan["name"], "status": plan["status"], "goal": body.goal,
            "exercises": _all_exercises(body), "start_date": plan["start_date"], "end_date": plan["end_date"],
            "current_phase": ph.name if ph else None,
            "current_week": ph.weeks[week_index(ph, today)].week if ph else None,
            "author": plan["author"], "updated_at": plan["updated_at"]}


def full(plan: dict, store: A.Store, today: date) -> dict:
    body = plan["body"]
    return {**summary(plan, store, today), "units": S.unit(), "convention": S.convention_text(),
            "targets": body.targets, "diagnosis": body.diagnosis, "guardrails": body.guardrails,
            "notes": body.notes,
            "maxes": [{**{k: v for k, v in m.items() if k != "value_lb"}, "value": S.to_disp(m["value_lb"])}
                      for m in plan["maxes"]],
            "baseline": {ex: _disp_snapshot(s) for ex, s in plan["baseline"].items()},
            "phases": render_phases(plan),
            "definition": json.loads(body.model_dump_json()),
            "created_at": plan["created_at"]}


def active_for(conn, store: A.Store, exercise: str, today: date) -> list[dict]:
    out = []
    for pid in list_ids(conn, "active"):
        p = load(conn, pid)
        if exercise in _all_exercises(p["body"]):
            out.append(summary(p, store, today))
    return out
