# Self Hosted Strong Sidecar

A self-hosted companion for the [Strong](https://www.strong.app/) workout-tracking app. It gives you
deeper insight into the training data you already log there. Export your history from Strong as CSV,
drop it in a folder, and the sidecar gives you progress charts, data-quality review, training-program
tracking, and a JSON API with a Claude skill that can plan your next session or help you break a plateau.

> **Unofficial project.** Self Hosted Strong Sidecar is an independent, community-built tool. It is
> **not affiliated with, endorsed by, sponsored by, or connected to Strong or its developers.** It only
> reads CSV files that you export from the app yourself. See [Disclaimers](#disclaimers).

## Features

- **Progress charts**: top set, estimated 1RM and volume per exercise over time, with session history.
- **Incremental import**: Strong exports your full history every time; the sidecar applies only what's
  new or changed, and skips files it has already seen.
- **Data-quality review**: flags sessions that don't look like the rest of your history (every set
  suddenly jumped, or weights that look like lb↔kg conversions, e.g. at another gym). You decide:
  keep it, correct for a bar logged differently, or exclude it.
- **Training programs**: define programs as JSON (sets × reps × % of 1RM/training max), get concrete
  weights for your current max, and track adherence (prescribed vs. what you actually did, frequency,
  e1RM change, when a re-test is due).
- **Your conventions**: lb or kg, and whether your logged barbell weights include the bar.
- **API + Claude skill**: an LLM-friendly JSON API (documented in-app) and a downloadable
  [Claude](https://claude.com/) skill for questions like *"what should my next bench session be?"*.

Everything runs on your machine. There is no account, cloud service or telemetry.

## Requirements

- Python 3.10+
- Node.js 20.19+ and [Yarn](https://classic.yarnpkg.com/) (to build the web UI)
- A CSV export from the Strong app

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd web && yarn install && yarn build && cd ..

.venv/bin/python -m strong_app serve          # UI + API on http://127.0.0.1:8765 (API docs at /docs)
```

Optional, for UI development: `cd web && yarn dev` starts Vite on :5173 and proxies `/api` to :8765.

Then open **Settings** in the UI and set your units and bar convention (below).

## Getting your data in

1. In the Strong app, use its data export to save your workout history as a CSV file.
2. Drop the CSV into `strong-extracts/` (any filename).
3. Do one of these:
   - restart the server (it scans the folder on startup), or
   - click **Data → Scan folder** in the UI, or upload the file there, or
   - run `.venv/bin/python -m strong_app import`

Each new export is a superset of the last. Import is a delta keyed on each workout's start timestamp:
new workouts are added, workouts edited in the app are replaced, workouts deleted in the app are
hidden, and a file already imported is skipped.

## Your data stays local

Your data lives in `data/` (a SQLite database) and `strong-extracts/` (your CSV exports). Both are
git-ignored, so they never end up in a commit. Program definitions in `programs/` and the skill in
`.claude/skills/` are meant to be committed.

## Settings

Set these in the **Settings** tab (or `PUT /api/v1/settings`). Your logged data is never changed.

**Units: lb or kg.** Strong exports weights in kg regardless of your app setting, so the sidecar shows
them in the unit you use. Every weight in the UI, the API (`units` field on each response) and the
skill uses it. Thresholds and rounding follow the unit, e.g. 5 lb or 2.5 kg steps.

**Bar convention.** People log barbell lifts differently, so tell the sidecar which you do:
- **Include the bar** (default): you log the total load. A 20 kg bar with 20 kg on each side is 60.
- **Exclude the bar**: you log only the weight added, so the same setup is 40. Also set your usual
  bar weight.

This drives program weights (percentages apply to the actual load, then convert back to how you log)
and the default amount for bar corrections in Review.

## Review

Sessions get flagged for review when:
- **the whole session jumped**: both the top set and the typical set are ≥8–10% (and ≥10 lb / 5 kg)
  above the median of your previous 5 sessions, or
- **weights look like a unit conversion**: at least 2 sets, making up 25% or more of the session,
  are off your normal loading grid (5 / 2.5 lb or 2.5 / 1.25 / 1 kg), e.g. 176, 198 or 209 lb
  (80, 90 and 95 kg).

In the **Review** tab, each flagged session gets one of:
- **OK (real)**: use it as-is.
- **Bar included in log → subtract** / **Bar left out of log → add**: the session was logged
  differently from your usual convention (e.g. at another gym). The bar amount (default: your
  Settings bar weight, editable) is corrected so it's comparable with the rest of your history.
- **Exclude**: different machine, bad data, etc.

Unreviewed flagged sessions are left out of trends, records and coach-context until you decide.
Decisions are stored separately from the imported data, so they survive re-imports.
Detection thresholds can be tuned with env vars `STRONG_TOP_JUMP_PCT`, `STRONG_MEDIAN_JUMP_PCT`,
`STRONG_JUMP_MIN` (in your display unit), `STRONG_BASELINE_SESSIONS`.

## Programs

Programs are JSON files in `programs/`. Edit them there, in the **Programs** tab, or via
`PUT /api/v1/programs/{slug}`. Included examples:
- `ufpwrlifter-bench`: the UFPwrLifter bench press program by Long Nguyen (3×/week, % of 1RM),
  transcribed from a [public repost](https://ornatestrength.fitness.blog/2019/12/10/ufpwrlifter-bench-press-program/).
- `wendler-531-bench`: the main-lift percentages of Jim Wendler's 5/3/1 on a 90% training max.

A program is weeks → days → exercises → sets. Each set has `sets`, `reps` (optionally `reps_max`,
`amrap`), and `pct` (of 1RM or training max) or an absolute `weight_lb` / `weight_kg`, plus optional
`rpe` and `warmup`. Each exercise uses a `lift` key, and `lifts` maps it to your Strong exercise
name. Rounding is `rounding_lb` (default 5) / `rounding_kg` (default 2.5); per-cycle increases are
`cycle_increment_lb` / `cycle_increment_kg` (one is converted if the other is missing). Percentages
are unit-free, so a program works for both lb and kg users. See the files for complete examples.

**Following a program** (Programs tab → *Start following a program*, or `POST /api/v1/assignments`)
records which program you run for which exercise since when (past dates are fine). The sidecar then
compares each logged session with the prescribed one (on target / lighter / heavier / fewer reps),
tracks frequency against the program's, e1RM change since starting, and whether a re-test is due.
It also shows the next prescribed sessions with weights. Without a fixed starting max, each session
is compared against your best estimated 1RM from the 90 days before it.

## API

Weights are in your display units (see the `units` field). `{exercise}` is the exact Strong exercise
name or its slug, e.g. `bench-press-barbell`.

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/overview` | Start here: history span, routines, every exercise with current level and trend |
| `GET /api/v1/exercises/{exercise}/coach-context` | LLM-ready: records, rep maxes, progression and stall signals, recent sessions |
| `GET /api/v1/exercises/{exercise}/program-context` | Coach context + the program you follow + adherence + next sessions |
| `GET /api/v1/exercises/{exercise}/sessions` | Set-by-set history |
| `GET /api/v1/exercises/{exercise}/series` | Chart-ready time series |
| `GET /api/v1/workouts`, `/api/v1/workouts/{id}` | Workouts with all sets |
| `GET/PUT/DELETE /api/v1/programs[/{slug}]`, `GET /api/v1/programs/{slug}/plan` | Programs and concrete weights |
| `GET/POST /api/v1/assignments`, `PATCH/DELETE /api/v1/assignments/{id}` | Which program you follow |
| `GET /api/v1/anomalies?status=pending\|reviewed\|all`, `PUT /api/v1/anomalies` | Flagged sessions and review decisions |
| `GET/PUT /api/v1/settings` | Units and bar convention |
| `POST /api/v1/import`, `POST /api/v1/import/scan`, `GET /api/v1/imports` | Imports |
| `GET /api/v1/export/sets.csv` | Flat CSV of every set (normalized) |
| `GET /api/v1/catalog` | This endpoint list as JSON (what each is for, params, returns) |
| `GET /api/v1/skill.zip`, `GET /api/v1/skill.md` | Download the Claude skill / view its SKILL.md |

Add `as_of=YYYY-MM-DD` to the overview, exercise or program-context endpoints to evaluate as of a past date.

The **API & Skill** tab in the UI documents every endpoint with a "Try it" button. It's rendered from
`strong_app/catalog.py`, which also generates the skill's `references/api.md`, so update that file when
adding endpoints. To regenerate the repo copy:
`.venv/bin/python -c "from strong_app.catalog import to_markdown; open('.claude/skills/strong-coach/references/api.md','w').write(to_markdown('http://127.0.0.1:8765'))"`

The API has **no authentication**. It's meant for `127.0.0.1`. Don't expose it to a network you don't
trust without putting authentication in front of it.

## Claude skill

`.claude/skills/strong-coach/` is active automatically when using [Claude Code](https://claude.com/claude-code)
in this project. Ask things like *"what should my next bench session be?"* or *"I've been following
UFPwrLifter for bench since March; how do I break my plateau?"*.

To use it everywhere, download it from the UI (or `/api/v1/skill.zip`; the download has your
server address and install path filled in) and run `unzip -o strong-coach.zip -d ~/.claude/skills/`.
Claude's web and desktop apps run skills in a cloud sandbox that can't reach `127.0.0.1`, so the
skill only works there if the API is reachable from the internet (download it with
`?base_url=https://your-host`), which needs the authentication noted above.

When you use the skill, the workout data it reads from the API becomes part of your conversation with
Claude and is handled under the terms of the Claude product you use.

## Project layout

```
strong_app/      Python backend (FastAPI): import, analysis, programs, API, settings
web/             Vite + vanilla JS UI (yarn build → strong_app/static)
programs/        Program definitions (JSON)
.claude/skills/  The strong-coach Claude skill
strong-extracts/ Your Strong CSV exports (git-ignored)
data/            SQLite database (git-ignored)
```

## Disclaimers

- **Not affiliated with Strong.** This is an independent, unofficial project. It is not affiliated
  with, endorsed by, sponsored by, or connected to Strong or its developers. "Strong" and related
  names and marks belong to their respective owners. They're used here only to say which app's
  export format the sidecar reads. Please don't contact Strong's support about this project.
- **Your export, your data.** The sidecar only reads CSV files you export from the Strong app. It
  doesn't connect to Strong's servers, use any Strong account or API, or modify the app. Strong may
  change its export format at any time, which could break imports.
- **Not medical or professional advice.** Charts, estimates (e.g. estimated 1RM), flags, program
  weights and anything the Claude skill suggests are informational and can be wrong. Strength
  training carries a risk of injury. Use your own judgment, train within your limits, and consult a
  qualified professional (physician, physical therapist or coach) before changing your training,
  especially if you have health conditions or injuries.
- **AI output.** Suggestions from the Claude skill are generated by an AI model from your data and
  may be inaccurate or inappropriate for you. Review them critically.
- **Programs belong to their authors.** The included program files are transcriptions of publicly
  available programs, credited to their authors, for personal use. They aren't endorsed by those
  authors and may contain transcription errors, so check them against the original source.
- **No warranty.** The software is provided "as is", without warranty of any kind. See [LICENSE](LICENSE).

## License

[MIT](LICENSE)
