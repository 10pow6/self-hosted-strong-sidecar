"""Import Strong CSV exports incrementally.

Strong always exports the full history, so each new file is a superset of the
last. We key workouts by start timestamp and hash their rows:
  * new timestamp           -> insert
  * same timestamp, new hash -> workout was edited in the app; replace its sets
  * in DB but not in file   -> deleted in the app; flagged missing_from_source
                               (kept, but hidden from analysis)
Re-importing a file with an already-seen sha256 is a no-op.
"""
from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
from pathlib import Path

from .db import EXTRACTS_DIR, bump_version

LB_PER_KG = 1 / 0.45359237
EXPECTED_HEADER = [
    "Workout #", "Date", "Workout Name", "Duration (sec)", "Exercise Name",
    "Set Order", "Weight (kg)", "Reps", "RPE", "Distance (meters)", "Seconds",
    "Notes", "Workout Notes",
]


def _num(v: str, cast=float):
    v = (v or "").strip()
    if v == "":
        return None
    try:
        return cast(float(v)) if cast is int else cast(v)
    except ValueError:
        return None


def parse_csv(text: str) -> dict[str, dict]:
    """Return {started_at: {meta..., rows: [...]}} preserving row order."""
    sample = text[:2048]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    missing = [h for h in EXPECTED_HEADER[:8] if h not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"Not a Strong export; missing columns: {missing}")

    workouts: dict[str, dict] = {}
    for r in reader:
        started = r["Date"].strip()
        w = workouts.setdefault(started, {
            "started_at": started,
            "workout_number": _num(r.get("Workout #"), int),
            "name": r.get("Workout Name", "").strip(),
            "duration_sec": _num(r.get("Duration (sec)"), int),
            "notes": (r.get("Workout Notes") or "").strip() or None,
            "rows": [],
            "raw": [],
        })
        w["raw"].append("\x1f".join((r.get(h) or "") for h in EXPECTED_HEADER))
        so = (r.get("Set Order") or "").strip()
        if not so.isdigit():          # e.g. "Rest Timer" pseudo-rows
            continue
        kg = _num(r.get("Weight (kg)"))
        w["rows"].append({
            "exercise": r["Exercise Name"].strip(),
            "set_order": int(so),
            "weight_kg": kg,
            "weight_lb": round(kg * LB_PER_KG, 1) if kg is not None else None,
            "reps": _num(r.get("Reps"), int),
            "rpe": _num(r.get("RPE")),
            "distance_m": _num(r.get("Distance (meters)")),
            "seconds": _num(r.get("Seconds")),
            "notes": (r.get("Notes") or "").strip() or None,
        })
    for w in workouts.values():
        w["content_hash"] = hashlib.sha256("\n".join(w.pop("raw")).encode()).hexdigest()
    return workouts


def import_text(conn: sqlite3.Connection, text: str, filename: str) -> dict:
    sha = hashlib.sha256(text.encode()).hexdigest()
    prev = conn.execute("SELECT * FROM imports WHERE sha256=?", (sha,)).fetchone()
    if prev:
        return {"status": "already_imported", "filename": filename, "import_id": prev["id"]}

    workouts = parse_csv(text)
    cur = conn.cursor()
    cur.execute("INSERT INTO imports(filename, sha256) VALUES (?, ?)", (filename, sha))
    import_id = cur.lastrowid

    existing = {r["started_at"]: (r["id"], r["content_hash"])
                for r in conn.execute("SELECT id, started_at, content_hash FROM workouts")}
    new = changed = 0
    new_dates: list[str] = []
    for started, w in workouts.items():
        if started in existing:
            wid, h = existing[started]
            if h == w["content_hash"]:
                cur.execute("UPDATE workouts SET last_import_id=?, missing_from_source=0, "
                            "workout_number=? WHERE id=?", (import_id, w["workout_number"], wid))
                continue
            changed += 1
            cur.execute("DELETE FROM sets WHERE workout_id=?", (wid,))
            cur.execute("UPDATE workouts SET workout_number=?, name=?, duration_sec=?, notes=?, "
                        "content_hash=?, last_import_id=?, missing_from_source=0 WHERE id=?",
                        (w["workout_number"], w["name"], w["duration_sec"], w["notes"],
                         w["content_hash"], import_id, wid))
        else:
            new += 1
            new_dates.append(started)
            cur.execute("INSERT INTO workouts(started_at, workout_number, name, duration_sec, notes, "
                        "content_hash, first_import_id, last_import_id) VALUES (?,?,?,?,?,?,?,?)",
                        (started, w["workout_number"], w["name"], w["duration_sec"], w["notes"],
                         w["content_hash"], import_id, import_id))
            wid = cur.lastrowid
        cur.executemany(
            "INSERT INTO sets(workout_id, seq, exercise, set_order, weight_kg, weight_lb, reps, rpe, "
            "distance_m, seconds, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(wid, i, s["exercise"], s["set_order"], s["weight_kg"], s["weight_lb"], s["reps"],
              s["rpe"], s["distance_m"], s["seconds"], s["notes"]) for i, s in enumerate(w["rows"])])

    gone = [s for s in existing if s not in workouts]
    if gone:
        cur.executemany("UPDATE workouts SET missing_from_source=1 WHERE started_at=?",
                        [(s,) for s in gone])
    cur.execute("UPDATE imports SET row_count=?, workouts_total=?, workouts_new=?, "
                "workouts_changed=?, workouts_missing=? WHERE id=?",
                (sum(len(w["rows"]) for w in workouts.values()), len(workouts), new, changed,
                 len(gone), import_id))
    bump_version(conn)
    conn.commit()
    return {"status": "imported", "filename": filename, "import_id": import_id,
            "workouts_total": len(workouts), "workouts_new": new, "workouts_changed": changed,
            "workouts_missing": len(gone),
            "new_workout_dates": sorted(new_dates)[-50:]}


def import_file(conn: sqlite3.Connection, path: Path) -> dict:
    return import_text(conn, Path(path).read_text(encoding="utf-8-sig"), Path(path).name)


def scan_folder(conn: sqlite3.Connection, folder: Path | None = None) -> list[dict]:
    """Import every CSV in the extracts folder, oldest first (already-seen files are skipped)."""
    folder = Path(folder or EXTRACTS_DIR)
    files = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime)
    return [import_file(conn, p) for p in files]
