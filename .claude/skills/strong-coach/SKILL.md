---
name: strong-coach
description: Coach from the user's Strong workout history via the local Self Hosted Strong Sidecar API. Use when the user asks what to do next for a lift, whether to add weight, how an exercise is progressing, why they're stalling or how to break a plateau, or how they're doing on a training program (e.g. "I've been following X for bench, how do I break my plateau?"), or wants a program's sets turned into weights.
---

# Strong coach

The user's lifting history (from Strong app CSV exports) is served by a local Self Hosted Strong
Sidecar API, an unofficial tool not affiliated with Strong. Never present its output as coming from Strong.
Base URL: `$STRONG_API` if set, else `http://127.0.0.1:8765`.

## 1. Make sure the API is up

```bash
curl -sf ${STRONG_API:-http://127.0.0.1:8765}/api/v1/imports >/dev/null || echo DOWN
```
If it's down, start it (in the background), then retry:
`cd ${STRONG_TRACKER_ROOT:-.} && .venv/bin/python -m strong_app serve`

Full endpoint reference: `references/api.md` next to this file, or live at `$B/api/v1/catalog`.

## 2. Gather data

```bash
B=${STRONG_API:-http://127.0.0.1:8765}
curl -s $B/api/v1/settings                                   # bar convention: read this first
curl -s $B/api/v1/overview                                   # routines, frequency, every exercise + trend
curl -s "$B/api/v1/exercises/<slug>/coach-context?recent=12" # the main input for one exercise
curl -s "$B/api/v1/exercises/<slug>/sessions?limit=20"       # full set-by-set detail if needed
curl -s "$B/api/v1/workouts?limit=10"                        # what was trained recently (fatigue/overlap)
```
For program / plateau questions also:
```bash
curl -s $B/api/v1/programs                                   # available programs
curl -s $B/api/v1/assignments                                # which program the user follows, since when
curl -s "$B/api/v1/exercises/<slug>/program-context"         # history + program + adherence + next sessions
curl -s "$B/api/v1/exercises/<slug>/program-context?program=<program-slug>&since=YYYY-MM-DD"  # ad hoc
curl -s "$B/api/v1/programs/<program-slug>/plan"             # program with concrete weights
```
- `<slug>` is the lowercase exercise name with non-alphanumerics turned into `-`
  (for example `Bench Press (Barbell)` becomes `bench-press-barbell`). Find slugs in `overview.exercises[].slug`.
- If the user names an exercise loosely ("bench", "curls"), pick from `overview.exercises`;
  when several match (barbell or dumbbell bench), ask which one or cover both briefly.

## 3. Read it correctly

- All weights are in the user's **display units** (`units` in every response: `lb` or `kg`), as the
  user logs them. Always state the unit. `GET /api/v1/settings` also says whether logged barbell
  numbers include the bar (`bar_tracking: included`) or not (`excluded`, so actual load = logged +
  `bar_weight`). `how_to_read` in coach-context repeats this. Prescribe in the user's own units and
  convention. If they say they're training somewhere their logging differs (e.g. a different bar or
  unit), also give the load to put on the bar.
- Sessions logged the other way have already been corrected (`adjustment` field), so numbers are comparable.
- Dumbbell weights are per dumbbell.
- `e1rm` is an Epley estimate. It's reliable for ≤12 reps and rough above that.
- Sessions with `review_status: pending` are unreviewed suspicious jumps (e.g. a different gym or
  unit conversions). They are **excluded from stats/trends**. Don't base progression on them. If
  `data_quality.pending_review` is non-empty, mention it and suggest reviewing in the web UI (Review tab).
- Key signals in `progression`:
  - `sessions_at_current_top_in_last_10` + `reps_at_current_top_recent` show whether they own the current weight.
  - `sessions_since_matching_all_time_top` and `e1rm_trend_per_4wk_90d` (≤0 means stalled or regressing).
  - `all_time_top_weight_milestones` shows how often they have historically been able to add weight.
- `frequency.median_days_between_sessions_recent` and `summary.days_since_last` matter: after a
  long gap (>10–14 days) suggest coming back at ~90% rather than progressing.
- Look at the **structure** of recent sessions (`recent_sessions[].sets`): warm-up ladder, top
  singles/heavy sets, back-off sets. Keep the user's established structure unless there's a reason to change it.

## 4. Programs and plateaus

When the user mentions a program ("I've been following X"):
1. Find it in `/api/v1/programs`. If it's missing, ask for the details (or a link), then offer to
   add it with `PUT /api/v1/programs/<slug>` (show the user the JSON first; don't invent percentages).
2. If there's no assignment for that exercise, use program-context ad hoc (`?program=…&since=…`).
   Ask roughly when they started if they didn't say. Offer to save it (`POST /api/v1/assignments`),
   but only write after the user agrees.
3. Read `programs[].adherence` honestly. "Following relatively closely" often isn't:
   - `actual_sessions_per_week` vs `expected_sessions_per_week` (frequency is a common gap)
   - `verdict_counts` and per-session `prescribed` vs `actual`: heavier tops with lighter
     back-offs, skipped sets, wrong percentages
   - `e1rm.change_since_start`, and `retest_due` / `retest_rule` (stale maxes make every % wrong)
   - matching is sequential (see `matching_note`), so treat single-session verdicts loosely.
4. Diagnose the plateau from the data first (adherence, stale max, frequency, recovery gaps,
   rep-range monotony, too much near-max work), then suggest changes. Options include re-testing
   or adjusting the max, fixing the gap you found, a short deload, changing rep ranges or
   variations, or switching programs (check what's in `/api/v1/programs`). Use
   `programs[].adherence.upcoming` or `/plan` for concrete weights.

## 5. Recommend

Give a concrete next session in the user's own format, e.g.

```
Bench Press (Barbell)
Warm-up: 70x15, 120x8, 150x5, 160x3, 170x1
Top:     205x1, 200x1
Back-off: 170x3, 150x7
```

State the bar convention when it matters (e.g. "weights as you log them; add your bar").
Then add 2–4 short bullets of rationale tied to specific numbers from the data (dates, weights,
reps, trend, adherence). Use a sensible progression rule: add the smallest increment the user
typically uses (e.g. 5 lb / 2.5 kg on a barbell, the next dumbbell up) when the top weight has been hit cleanly for
≥2–3 sessions. Hold if it's recent or reps are dropping. Deload ~10% if the e1RM trend is negative
for 4+ weeks or the user is coming back from a break. Say what to look for next time (for example
"if 205x1 moves well twice, try 210").

Don't invent data. If the API lacks something (RPE, bodyweight, injuries), ask or say so.
