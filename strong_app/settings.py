"""User settings (stored in the local DB). Personal conventions belong here, not in code.

Units: stored quantities (DB, program files) are canonical lb. Analysis and the API work in
the user's display unit; to_disp()/from_disp() convert at the edges.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field

KG_PER_LB = 0.45359237


class Settings(BaseModel):
    """Stored form (canonical lb)."""
    units: Literal["lb", "kg"] = "lb"
    bar_tracking: Literal["included", "excluded"] = "included"
    bar_weight_lb: float = Field(45, ge=0, le=100)


class SettingsIn(BaseModel):
    """API form: bar_weight is in `units`."""
    units: Literal["lb", "kg"] = Field("lb", description="Display units for all weights")
    bar_tracking: Literal["included", "excluded"] = Field(
        "included",
        description="How you log barbell lifts in Strong: 'included' = the logged number is the total "
                    "load including the bar; 'excluded' = you log only the weight added, not the bar.")
    bar_weight: float = Field(
        45, ge=0, le=100,
        description="Weight of the bar you normally use, in `units`. With 'excluded' it is added back to "
                    "get the actual load; it's also the default amount for bar corrections in review.")


CURRENT = Settings()


def load(conn: sqlite3.Connection) -> Settings:
    global CURRENT
    stored = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}
    CURRENT = Settings(**{k: v for k, v in stored.items() if k in Settings.model_fields})
    return CURRENT


def save(conn: sqlite3.Connection, s: Settings) -> Settings:
    global CURRENT
    with conn:
        for k, v in s.model_dump().items():
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))
    CURRENT = s
    return CURRENT


def from_api(s: SettingsIn) -> Settings:
    lb = s.bar_weight if s.units == "lb" else s.bar_weight / KG_PER_LB
    return Settings(units=s.units, bar_tracking=s.bar_tracking, bar_weight_lb=round(lb, 3))


def to_api() -> dict:
    s = CURRENT
    return {"units": s.units, "bar_tracking": s.bar_tracking, "bar_weight": to_disp(s.bar_weight_lb),
            "convention": convention_text()}


# ---- unit helpers ---------------------------------------------------------------------
def unit() -> str:
    return CURRENT.units


def other_unit() -> str:
    return "lb" if CURRENT.units == "kg" else "kg"


def rnd(x: float) -> float:
    """Round a weight for display precision in the current unit."""
    return round(x, 2 if CURRENT.units == "kg" else 1)


def to_disp(lb: float | None) -> float | None:
    if lb is None:
        return None
    return rnd(lb * KG_PER_LB) if CURRENT.units == "kg" else rnd(lb)


def from_disp(x: float | None) -> float | None:
    if x is None:
        return None
    return x / KG_PER_LB if CURRENT.units == "kg" else x


def jump_min() -> float:
    """Minimum absolute jump that can count as suspicious."""
    return 5.0 if CURRENT.units == "kg" else 10.0


def load_grids() -> tuple[float, ...]:
    """Candidate loading increments, coarse to fine."""
    return (2.5, 1.25, 1.0) if CURRENT.units == "kg" else (5.0, 2.5)


def bar_weight() -> float:
    return to_disp(CURRENT.bar_weight_lb)


def bar_offset() -> float:
    """Amount (display units) to ADD to a logged barbell weight to get the actual load."""
    return bar_weight() if CURRENT.bar_tracking == "excluded" else 0.0


def convention_text() -> str:
    u = unit()
    if CURRENT.bar_tracking == "excluded":
        return (f"Weights are in {u}. Barbell lifts are logged WITHOUT the bar: actual load = logged + "
                f"{bar_weight():g} {u} (the user's usual bar).")
    return f"Weights are in {u}. Barbell lifts are logged as the total load including the bar."
