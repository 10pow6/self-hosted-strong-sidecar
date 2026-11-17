"""Turn raw sets into per-session metrics, detect suspicious jumps, build coach context.

All weights are in the user's display unit (settings: lb or kg). "Normalized" weights have
review decisions applied, and are what charts and the coaching endpoints use.

The user's bar convention (bar included in the logged number or not) lives in
settings.py. Sessions logged the other way are corrected by a review decision:
bar_included -> subtract the bar, bar_excluded -> add it.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import median

from . import settings as S
from .db import data_version

# ---- detection settings (env-overridable) ---------------------------------
CFG = {
    "baseline_sessions": int(os.environ.get("STRONG_BASELINE_SESSIONS", 5)),   # look-back sessions
    "baseline_min_sessions": 3,                                               # need this many to judge
    "baseline_max_age_days": int(os.environ.get("STRONG_BASELINE_DAYS", 120)),
    "top_jump_pct": float(os.environ.get("STRONG_TOP_JUMP_PCT", 0.08)),        # top set +8% ...
    "median_jump_pct": float(os.environ.get("STRONG_MEDIAN_JUMP_PCT", 0.10)),  # typical set +10% ...
    # ... and at least this much in display units (default: 10 lb / 5 kg)
    "jump_min": float(os.environ["STRONG_JUMP_MIN"]) if os.environ.get("STRONG_JUMP_MIN") else None,
}
BAR_ADJUST = {"bar_included": -1, "bar_excluded": +1}   # review status -> sign of the bar correction


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def epley(w: float, r: int) -> float:
    return w if r == 1 else w * (1 + r / 30)


@dataclass
class SetRow:
    set_order: int
    weight_raw: float          # as logged, display units
    weight: float              # normalized, display units
    reps: int | None
    rpe: float | None = None
    seconds: float | None = None
    distance_m: float | None = None
    notes: str | None = None

    def as_dict(self, include_raw: bool) -> dict:
        d = {"set": self.set_order, "weight": self.weight, "reps": self.reps}
        if include_raw:
            d["logged_weight"] = self.weight_raw
        for k in ("rpe", "seconds", "distance_m", "notes"):
            v = getattr(self, k)
            if v not in (None, 0, 0.0):
                d[k] = v
        return d


@dataclass
class Session:
    workout_id: int
    started_at: str
    workout_name: str
    exercise: str
    sets: list[SetRow] = field(default_factory=list)
    # review
    flag_reasons: list[str] = field(default_factory=list)
    baseline: dict | None = None
    status: str | None = None       # None | pending | ok | bar_included | bar_excluded | exclude
    bar_weight: float | None = None  # display units
    note: str | None = None
    metrics: dict = field(default_factory=dict)
    raw_metrics: dict = field(default_factory=dict)

    @property
    def day(self) -> date:
        return datetime.fromisoformat(self.started_at).date()

    @property
    def key(self) -> str:
        return f"{self.started_at}|{self.exercise}"

    @property
    def excluded(self) -> bool:
        return self.status == "exclude"

    @property
    def adjusted(self) -> bool:
        return self.status in BAR_ADJUST


def compute_metrics(sets: list[SetRow], use_raw: bool = False) -> dict:
    done = [s for s in sets if (s.reps or 0) > 0]
    ws = [(s.weight_raw if use_raw else s.weight, s.reps) for s in done]
    total_reps = sum(r for _, r in ws)
    weighted = [(w, r) for w, r in ws if w and w > 0]
    m = {"set_count": len(done), "total_reps": total_reps,
         "max_reps": max((r for _, r in ws), default=0)}
    if not weighted:
        m["bodyweight"] = True
        return m
    top = max(w for w, _ in weighted)
    e1rm_w, e1rm_r = max(((w, r) for w, r in weighted if r <= 30),
                         key=lambda x: epley(*x), default=(None, None))
    m.update({
        "bodyweight": False,
        "top_weight": top,
        "top_set_reps": max(r for w, r in weighted if w == top),
        "median_weight": S.rnd(median(w for w, _ in weighted)),
        "volume": round(sum(w * r for w, r in weighted)),
        "e1rm": S.rnd(epley(e1rm_w, e1rm_r)) if e1rm_w else None,
        "e1rm_from": f"{e1rm_w:g}x{e1rm_r}" if e1rm_w else None,
        "working_sets": [f"{w:g}x{r}" for w, r in weighted if w >= 0.85 * top],
    })
    return m


def _weight_grid(sessions: list[Session]) -> float | None:
    """Increment the user normally loads in (e.g. 5 lb or 2.5 kg), if they're consistent."""
    ws = [s.weight_raw for sess in sessions for s in sess.sets if s.weight_raw and s.weight_raw > 0]
    if len(ws) < 20:
        return None
    for g in S.load_grids():
        on = sum(1 for w in ws if abs(w / g - round(w / g)) < 0.02)
        if on / len(ws) >= 0.9:
            return g
    return None


def _off_grid(w: float, g: float) -> bool:
    return abs(w / g - round(w / g)) >= 0.02


class Store:
    """Loads everything into memory and runs detection; cached per data version."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._lock = threading.Lock()
        self._version = -1
        self.by_exercise: dict[str, list[Session]] = {}
        self.slugs: dict[str, str] = {}
        self.workouts: dict[int, dict] = {}

    def invalidate(self):
        self._version = -1

    def refresh(self) -> "Store":
        with self._lock:
            v = data_version(self.conn)
            if v != self._version:
                self._load()
                self._version = v
        return self

    # ------------------------------------------------------------------
    def _load(self):
        anns = {(r["started_at"], r["exercise"]): dict(r)
                for r in self.conn.execute("SELECT * FROM annotations")}
        rows = self.conn.execute(
            "SELECT w.id wid, w.started_at, w.name, w.duration_sec, w.notes wnotes, w.workout_number, "
            "s.exercise, s.set_order, s.weight_lb, s.weight_kg, s.reps, s.rpe, s.seconds, s.distance_m, s.notes "
            "FROM sets s JOIN workouts w ON w.id = s.workout_id "
            "WHERE w.missing_from_source = 0 ORDER BY w.started_at, s.seq").fetchall()
        sessions: dict[tuple, Session] = {}
        workouts: dict[int, dict] = {}
        kg = S.unit() == "kg"
        for r in rows:
            workouts.setdefault(r["wid"], {
                "id": r["wid"], "started_at": r["started_at"], "name": r["name"],
                "duration_min": round((r["duration_sec"] or 0) / 60), "notes": r["wnotes"],
                "workout_number": r["workout_number"], "exercises": []})
            k = (r["started_at"], r["exercise"])
            if k not in sessions:
                sessions[k] = Session(r["wid"], r["started_at"], r["name"], r["exercise"])
                workouts[r["wid"]]["exercises"].append(r["exercise"])
            # Strong stores kg; weight_lb is that converted back to what lb users typed.
            w = (round(r["weight_kg"] or 0.0, 2) if kg else r["weight_lb"]) or 0.0
            sessions[k].sets.append(SetRow(r["set_order"], w, w, r["reps"], r["rpe"],
                                           r["seconds"], r["distance_m"], r["notes"]))
        by_ex: dict[str, list[Session]] = {}
        for s in sessions.values():
            by_ex.setdefault(s.exercise, []).append(s)
        for ex, lst in by_ex.items():
            self._detect(lst, anns)
        self.by_exercise = dict(sorted(by_ex.items(), key=lambda kv: -len(kv[1])))
        self.slugs = {slugify(e): e for e in self.by_exercise}
        self.workouts = workouts

    def _detect(self, sessions: list[Session], anns: dict):
        grid = _weight_grid(sessions)
        jump_min = CFG["jump_min"] if CFG["jump_min"] is not None else S.jump_min()
        pool: list[Session] = []   # accepted sessions forming the baseline
        n, max_age = CFG["baseline_sessions"], CFG["baseline_max_age_days"]
        for s in sessions:
            s.raw_metrics = compute_metrics(s.sets, use_raw=True)
            raw = s.raw_metrics
            reasons: list[str] = []
            recent = [p for p in pool[-n:] if (s.day - p.day).days <= max_age]
            if not raw.get("bodyweight") and len(recent) >= CFG["baseline_min_sessions"]:
                b_top = S.rnd(median(p.metrics["top_weight"] for p in recent))
                b_med = S.rnd(median(p.metrics["median_weight"] for p in recent))
                s.baseline = {"top_weight": b_top, "median_weight": b_med,
                              "sessions": len(recent), "from": recent[0].started_at[:10],
                              "to": recent[-1].started_at[:10]}
                d_top = S.rnd(raw["top_weight"] - b_top)
                d_med = S.rnd(raw["median_weight"] - b_med)
                # "Every set jumped": both the top set and the typical set are well above
                # the recent norm. Progression usually moves only the top sets.
                if (d_top >= max(jump_min, CFG["top_jump_pct"] * b_top)
                        and d_med >= max(jump_min, CFG["median_jump_pct"] * b_med)):
                    reasons.append(f"whole session jumped: top set {raw['top_weight']:g} vs usual {b_top:g} "
                                   f"(+{d_top:g}), typical set {raw['median_weight']:g} vs usual {b_med:g} "
                                   f"(+{d_med:g} {S.unit()})")
            if grid and not raw.get("bodyweight"):
                odd = sorted({x.weight_raw for x in s.sets if x.weight_raw > 0 and _off_grid(x.weight_raw, grid)})
                n_w = sum(1 for x in s.sets if x.weight_raw > 0)
                n_odd = sum(1 for x in s.sets if x.weight_raw in odd)
                if n_odd >= 2 and n_odd >= 0.25 * n_w:
                    reasons.append(f"unusual increments {', '.join(f'{w:g}' for w in odd)} "
                                   f"(you normally load in {grid:g} {S.unit()} steps; "
                                   f"{S.other_unit()} conversion?)")
            s.flag_reasons = reasons

            ann = anns.get((s.started_at, s.exercise))
            if ann:
                s.status, s.note = ann["status"], ann["note"]
                if ann["status"] in BAR_ADJUST:
                    # stored canonical lb -> display units
                    s.bar_weight = (S.to_disp(ann["bar_weight_lb"]) if ann["bar_weight_lb"] is not None
                                    else S.bar_weight())
                    delta = BAR_ADJUST[ann["status"]] * s.bar_weight
                    for x in s.sets:
                        if x.weight_raw > 0:
                            x.weight = S.rnd(max(x.weight_raw + delta, 0))
            elif reasons:
                s.status = "pending"
            s.metrics = compute_metrics(s.sets) if s.adjusted else raw
            # Unreviewed sessions still feed the baseline (as logged) so a genuine new
            # level stops being flagged after a few sessions; reviewed ones feed it
            # normalized. Only excluded sessions are left out.
            if s.status != "exclude" and not s.metrics.get("bodyweight"):
                pool.append(s)

    # ---- lookups -----------------------------------------------------
    def resolve(self, name_or_slug: str) -> str | None:
        if name_or_slug in self.by_exercise:
            return name_or_slug
        return self.slugs.get(slugify(name_or_slug))

    def all_sessions(self):
        for lst in self.by_exercise.values():
            yield from lst


# ---- serializers --------------------------------------------------------
def session_dict(s: Session, include_sets: bool = True) -> dict:
    d = {"date": s.started_at[:10], "started_at": s.started_at, "workout_id": s.workout_id,
         "workout_name": s.workout_name, **{k: v for k, v in s.metrics.items()}}
    if include_sets:
        d["sets"] = [x.as_dict(s.adjusted) for x in s.sets]
    if s.status:
        d["review_status"] = s.status
    if s.status == "bar_included":
        d["adjustment"] = f"logged including the bar; {s.bar_weight:g} {S.unit()} subtracted to match your usual logging"
    elif s.status == "bar_excluded":
        d["adjustment"] = f"logged without the bar; {s.bar_weight:g} {S.unit()} added to match your usual logging"
    if s.status == "pending":
        d["warning"] = "unreviewed suspicious jump: " + "; ".join(s.flag_reasons)
    return d


def anomaly_dict(s: Session) -> dict:
    return {"id": s.key, "started_at": s.started_at, "date": s.started_at[:10],
            "exercise": s.exercise, "slug": slugify(s.exercise), "workout_name": s.workout_name,
            "status": s.status, "reasons": s.flag_reasons, "baseline": s.baseline,
            "bar_weight": s.bar_weight, "note": s.note,
            "logged": {"top_weight": s.raw_metrics.get("top_weight"),
                       "median_weight": s.raw_metrics.get("median_weight"),
                       "sets": [f"{x.weight_raw:g}x{x.reps}" for x in s.sets]},
            "normalized": {"top_weight": s.metrics.get("top_weight"),
                           "e1rm": s.metrics.get("e1rm")}}


def _days_between(a: date, b: date) -> int:
    return (b - a).days


def _slope_per_4wk(points: list[tuple[date, float]]) -> float | None:
    if len(points) < 3:
        return None
    x0 = points[0][0]
    xs = [(d - x0).days for d, _ in points]
    ys = [v for _, v in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den * 28, 1)


def usable(sessions: list[Session]) -> list[Session]:
    return [s for s in sessions if s.status not in ("exclude", "pending")]


def exercise_summary(name: str, sessions: list[Session], today: date) -> dict:
    use = usable(sessions)
    last = sessions[-1]
    weighted = [s for s in use if not s.metrics.get("bodyweight")]
    recent90 = [s for s in use if _days_between(s.day, today) <= 90]
    d = {"exercise": name, "slug": slugify(name), "sessions_total": len(sessions),
         "first_date": sessions[0].started_at[:10], "last_date": last.started_at[:10],
         "days_since_last": _days_between(last.day, today),
         "sessions_last_30d": sum(1 for s in use if _days_between(s.day, today) <= 30),
         "sessions_last_90d": len(recent90),
         "bodyweight": not weighted,
         "pending_reviews": sum(1 for s in sessions if s.status == "pending")}
    if weighted:
        lw = weighted[-1].metrics
        d.update({"current_top_weight": lw["top_weight"], "current_e1rm": lw["e1rm"],
                  "best_e1rm": max((s.metrics["e1rm"] or 0) for s in weighted),
                  "e1rm_trend_per_4wk_90d": _slope_per_4wk(
                      [(s.day, s.metrics["e1rm"]) for s in weighted
                       if _days_between(s.day, today) <= 90 and s.metrics["e1rm"]])})
    else:
        d["current_max_reps"] = use[-1].metrics["max_reps"] if use else None
    return d


def coach_context(name: str, sessions: list[Session], today: date, recent_n: int = 12) -> dict:
    """Everything an LLM coach needs to suggest the next session for one exercise."""
    use = usable(sessions)
    weighted = [s for s in use if not s.metrics.get("bodyweight")]
    summary = exercise_summary(name, sessions, today)
    gaps = [_days_between(a.day, b.day) for a, b in zip(use[-9:], use[-8:])]
    out: dict = {
        "exercise": name, "slug": slugify(name), "units": S.unit(), "as_of": today.isoformat(),
        "how_to_read": ("Weights are as the user logs them. " + S.convention_text() + " Sessions "
                        "that were logged the other way have been corrected to the user's usual convention "
                        "(see 'adjustment'). e1rm = Epley estimated 1-rep max (sets up to 30 reps; rough "
                        "above ~12). Sessions marked exclude or still pending review are left out of "
                        "stats/trends but listed under data_quality."),
        "summary": summary,
        "frequency": {"median_days_between_sessions_recent": median(gaps) if gaps else None,
                      "sessions_last_30d": summary["sessions_last_30d"],
                      "sessions_last_90d": summary["sessions_last_90d"]},
    }
    if weighted:
        best_e1 = max(weighted, key=lambda s: s.metrics["e1rm"] or 0)
        heaviest = max(weighted, key=lambda s: (s.metrics["top_weight"], s.metrics["top_set_reps"]))
        rep_max: dict[str, dict] = {}
        for target in (1, 3, 5, 8, 10, 12, 15):
            best = None
            for s in weighted:
                for x in s.sets:
                    if x.weight > 0 and (x.reps or 0) >= target and (best is None or x.weight > best[0]):
                        best = (x.weight, x.reps, s.started_at[:10])
            if best:
                rep_max[f"{target}+_reps"] = {"weight": best[0], "reps": best[1], "date": best[2]}
        # progression of top working weight
        increases, prev_top, prev_date = [], None, None
        for s in weighted:
            t = s.metrics["top_weight"]
            if prev_top is not None and t > prev_top:
                increases.append({"date": s.started_at[:10], "from": prev_top, "to": t})
            if prev_top is None or t > prev_top:
                prev_top, prev_date = t, s.started_at[:10]
        last_top = weighted[-1].metrics["top_weight"]
        since_pr = 0
        for s in reversed(weighted):
            if s.metrics["top_weight"] >= prev_top:
                break
            since_pr += 1
        at_current = [s for s in weighted[-10:] if s.metrics["top_weight"] == last_top]
        out["records"] = {
            "heaviest_set": {"weight": heaviest.metrics["top_weight"],
                             "reps": heaviest.metrics["top_set_reps"], "date": heaviest.started_at[:10]},
            "best_e1rm": {"e1rm": best_e1.metrics["e1rm"], "from_set": best_e1.metrics["e1rm_from"],
                          "date": best_e1.started_at[:10]},
            "rep_maxes": rep_max,
        }
        out["progression"] = {
            "all_time_top_weight": prev_top, "all_time_top_weight_first_hit": prev_date,
            "sessions_since_matching_all_time_top": since_pr,
            "current_top_weight": last_top,
            "sessions_at_current_top_in_last_10": len(at_current),
            "reps_at_current_top_recent": [s.metrics["top_set_reps"] for s in at_current],
            "e1rm_trend_per_4wk_90d": summary.get("e1rm_trend_per_4wk_90d"),
            "e1rm_trend_per_4wk_365d": _slope_per_4wk(
                [(s.day, s.metrics["e1rm"]) for s in weighted
                 if _days_between(s.day, today) <= 365 and s.metrics["e1rm"]]),
            "all_time_top_weight_milestones": increases[-6:],
        }
    out["last_session"] = session_dict(sessions[-1]) if sessions else None
    out["recent_sessions"] = [
        {"date": s.started_at[:10], "workout": s.workout_name,
         "sets": ", ".join(f"{x.weight:g}x{x.reps}" if x.weight else f"BWx{x.reps}" for x in s.sets),
         **{k: s.metrics.get(k) for k in ("top_weight", "top_set_reps", "e1rm", "volume", "total_reps")
            if k in s.metrics}}
        for s in use[-recent_n:]]
    out["data_quality"] = {
        "pending_review": [anomaly_dict(s) for s in sessions if s.status == "pending"][-10:],
        "adjusted_sessions": sum(1 for s in sessions if s.adjusted),
        "excluded_sessions": sum(1 for s in sessions if s.excluded),
    }
    return out
