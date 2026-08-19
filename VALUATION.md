# VALUATION — running cost + worth of the AgOps ag-drone software

**This file is the single source of truth for what this project has cost and what it is worth.**
It exists so nobody has to take a rough stab at either number again. It is git-tracked in the
project repo, so it is reachable from any machine at
`github.com/taborbachelor/caleb-rc-project` → `VALUATION.md`.

- **Owner:** Tabor. **Audience:** Tabor + Jackson. **NOT for Caleb** — see *Negotiating position*.
- **Update it:** run `py tools\session_cost.py --new` at the end of any working session and append
  the rows it prints. Procedure at the bottom.
- **Last reconciled:** 2026-08-19, after the three-session day (session `b06ca0f4`).

---

## The four numbers

| | Amount | What it means |
|---|---:|---|
| **Token cost to date** | **$1,891** | Claude usage at Anthropic API list price. A billing proxy — real cash is far less. |
| **All-in cost to date** | **≈ $8,800** | Token cost + Tabor's hours at a conservative $75/hr. Under $500 of this is actual money. |
| **Replacement cost** | **$200k – $390k** | What a contract team would charge to rebuild it. The ceiling, and not collectable here. |
| **Recommended ask** | **$7,500** | Phase 1 fixed fee + retainer + royalty. See *Negotiating position*. |

---

## 1. Token cost ledger

Anthropic API list price. Claude Code actually runs on a Max plan, so this is **not** spend — it
is a defensible unit of account for the work, and it is the number the original billing table in
`CLAUDE-CALEB.md` tracked. Rates used: Opus tier $5/$25 per MTok in/out, Fable $10/$50,
Sonnet $3/$15; cache writes 1.25× (5 min) / 2× (1 hour) the input rate, cache reads 0.1×.

| Period | Work | Sessions | Basis | +20% margin |
|---|---|---|---:|---:|
| 2026-07-10 → 07-13 | Ag platform R1/R2, refinement audit, 44 workflow subagents | `29330544` | $1,182.69 | $1,419.23 |
| 2026-07-21 → 08-14 | Directive + M1a/M1b/M2/M3/M4, guardian, preflight gate, bench kit, soak, Vite + 3D UI | `58e2dfa5` `d2552655` `81187eb4` `5c00e666` | $144.99 | $173.99 |
| 2026-08-15 → 08-16 | Backend hardening (~35 findings), guardian EKF/vibration/airspeed monitors, docs + merge | 3 sessions, **not measurable** ¹ | ~$120 | ~$144 |
| 2026-08-18 | Lane A — guardian SITL proof, bank angle, wind, keepout proximity, scorecard, alert unification | `303e824a` | $129.26 | $155.11 |
| 2026-08-18 | Lane B — powerline keepouts, connector-leg rerouting, coverage analysis, cross-lane fix | `04d27772` | $179.05 | $214.86 |
| 2026-08-19 | Valuation + this ledger | `69aaf91a` | $16.49 ² | $19.79 |
| 2026-08-19 | — top-up of the row above, per footnote ² | `69aaf91a` | +$1.68 | +$2.02 |
| 2026-08-19 | AIR — onboard exclusion fences, MAVLink 2, guard hardening | `1681cef0` | $44.17 ³ | $53.00 |
| 2026-08-19 | UI — scorecard panel, one-verdict pre-flight, 3D orientation fix, docs close-out | `b06ca0f4` | $35.97 ³ | $43.16 |
| 2026-08-19 | PLANNER — turn-geometry bank constraint, headlands | `c63d3a97` | $35.71 | $42.85 |
| | | | **$1,891** | **$2,269** |

¹ Those three sessions ran on the previous laptop. Claude Code transcripts live under the Windows
profile of the machine that ran them (`~/.claude/projects/C--Users-<profile>-…/`), and that profile
is gone, so the rows are a considered estimate rather than a measurement. **Never let a re-run of
`session_cost.py` silently delete them** — the script can only see what is on the current machine.
Recoverable if that laptop ever comes back: see *Open items*.

³ Both of these were STILL RUNNING when this row was written, so both are low for the same
reason as footnote ². Top them up next session.

² A session cannot fully count itself — the row is priced at the moment it is written and the
session keeps spending afterward. Every last row in this table is therefore slightly low. Correct
it next session with `py tools\session_cost.py --session <id>`; `--new` will not surface it,
because the id is already in this file.

---

## 2. Labour ledger

The scarce input, and the one the token table completely omits. Hours are transcript wall clock —
first event to last — which is an **upper bound** on attended time, not a timesheet.

| Period | Hours | Source |
|---|---:|---|
| 2026-07-10 → 08-16 (all sessions on retired machines) | ~55 – 75 | Estimated: ~9 sessions, sized against the measured ones |
| 2026-08-18 Lane A `303e824a` | 8.7 | Measured |
| 2026-08-18 Lane B `04d27772` | 8.4 | Measured |
| 2026-08-19 `69aaf91a` | ~~10.3~~ | **Superseded by the row below** — it is the same calendar day. |
| 2026-08-19 (four overlapping sessions) | 12.4 | **UNION of the four spans, not their sum** — see the warning below |
| **Total attended** | **≈ 84 – 104 h** | Working figure: **92 h** |

At $75/hr (conservative internal rate) → **$6,900**. At $125/hr (market) → **$11,500**.

> ⚠️ **PARALLEL SESSIONS BREAK THE SUM-THE-WALL-CLOCK METHOD. Read this before appending again.**
> Until 2026-08-19 sessions ran one at a time, so summing each session's wall clock was a fair
> upper bound on attended time. On 2026-08-19 **four sessions ran concurrently** and the script
> reported 12.4 + 12.4 + 1.3 + 10.6 = **36.7 h for a single calendar day**. Adding that would have
> inflated the labour line — and therefore the all-in cost that anchors the negotiating position —
> by roughly a factor of three. The honest figure is the **union of the spans**: 04:25 to 16:49 on
> one machine = **12.4 h**, which is what the table now carries. `session_cost.py` prints per-session
> wall clock and cannot know sessions overlapped, so **whoever appends must check the start times
> and union any that do.** Token cost is unaffected — that spend is genuinely additive.

**All-in cost = token cost + labour ≈ $8,800** at the conservative rate. Cash actually spent is a
Claude Max subscription over six weeks, roughly $200–400.

---

## 3. What it's worth

Three frames, three answers, all legitimate — they answer different questions. Recompute these
when the *Inputs* below change, not by feel.

### Inputs (regenerate with the commands shown)

| Input | Value | Command |
|---|---:|---|
| Tracked source lines | 27,642 | `git ls-files \| grep -E "\.(py\|jsx\|js\|ts\|tsx\|css\|html\|ps1\|sh)$" \| xargs wc -l` |
| Backend Python | 16,553 | `git ls-files "backend/*.py" \| xargs wc -l` |
| GCS frontend | 5,334 | `git ls-files "frontend/*.jsx" "frontend/*.js" \| xargs wc -l` |
| Customer site (PrairieSpray) | 1,848 | `git ls-files "web/*" \| grep -E "\.(jsx\|js\|css\|html)$" \| xargs wc -l` |
| Backend unit tests | 419 | `grep -rc "def test_" backend/tests/*.py` |
| Live SITL scenarios | 15 | `backend\scenarios.ps1 all` |
| Frontend tests | 42 | `cd frontend && npm test` |
| API endpoints | 56 | `grep -rhoE '@router\.(get\|post\|put\|delete\|websocket)\("[^"]*"' backend/app/routers/` |
| Commits / working days | 79 / 10 | `git rev-list --count HEAD` |

### Frame A — Replacement cost · **$200k – $390k**

27,642 lines of specialist UAV / MAVLink / GIS / full-stack work, tested and simulator-validated,
at a sustained 10–15 lines per hour for production code including tests and debugging →
**1,843 – 2,764 engineer-hours**. At a blended US contract rate of $110–140/hr for that skill mix.
A lean two-person shop at ~$65/hr effective would be $120k – $180k.

Recomputed 2026-08-19 (+3,350 tracked lines in one day, past this file's own +2,000 trigger).
**Frames B and C are deliberately unchanged: no section-4 milestone landed.** Onboard exclusion
fences were proven against the simulator, not a real aircraft, and the spray layer still does not
exist — the two largest discounts both stand.

This is the honest cost to reproduce. It is **not** collectable from Caleb and should never be the
asking price — but it is the right number to *show* him, so the friend price reads as the discount
it is.

### Frame B — Standalone asset · **$25k – $60k**

What a third party would pay for the repo today. Discounted hard, and correctly, for:

- **Never flown a real aircraft.** All validation is SITL. Until first flight this is unproven.
- **The spray layer is absent.** Flow, section control, rate, as-applied verification: none of it
  exists. The thing the business actually sells is the one subsystem not built.
- No customers, no revenue, no operating company around it.
- Single airframe (ArduPlane); copter would need the airframe abstraction M5 defers.
- Free alternatives (Mission Planner, QGroundControl) cover the non-ag half competently.

### Frame C — Value to Caleb · **$10k – $40k**

The only frame that sets the price, because it is bounded by what the one buyer can pay. His
alternatives: a free open-source GCS plus a great deal of manual planning, or buying into the DJI
Agras ecosystem at $20k+ per aircraft and staying locked to it. Neither gives him the customer
ordering site, USDA field auto-detection, keepout-aware coverage planning, or the Guardian layer.

---

## 4. Milestones that move the number

This is what makes the valuation *running* rather than a snapshot. Revisit Frames B and C when any
of these lands.

| Milestone | Effect on Frame B | Why |
|---|---|---|
| First successful real-hardware flight | **+$15k – 30k** | Removes the single largest discount. Unproven → proven. |
| Spray hardware layer shipped + verified | **+$25k – 50k** | The subsystem the business sells; without it this is a GCS, not an ag platform. |
| First paying spray customer through PrairieSpray | **+$20k+** | Converts a codebase into a revenue-producing system; changes the buyer pool entirely. |
| Second operator licensed | **step change** | Proves it is a product, not a bespoke build. Frame B stops being the right frame. |
| Part 137 certificate in hand (Caleb's) | indirect | Doesn't raise the software's value; unblocks Frame C being collectable at all. |
| Copter airframe support (M5 abstraction) | **+$10k – 20k** | Roughly doubles the addressable operator base. |
| 12 months with no maintenance | **−** | Depreciates. ArduPilot moves; an unmaintained GCS rots. |

---

## 5. Negotiating position

Full reasoning: the *Valuing AgOps GCS* memo (19 Aug 2026). Terms in brief:

| Term | Number |
|---|---|
| Phase 1 fixed fee (everything through first flight) | **$7,500** — payable now, 3 × $2,500, or a note against first spray revenue |
| Ongoing development + support | **$750 – 1,500 / month**, month-to-month, cancellable either side |
| IP | **We keep it.** Caleb gets a licence; territory exclusivity for ag spraying is available and priced separately |
| Upside | **$0.25 – 0.50 per acre sprayed**, or 4% of spray revenue — **capped at $50k**, then paid-up perpetual |
| Partner alternative | 15–25% equity + royalty, no fee, no retainer — but only with a written scope of what we owe |

**Rules that keep us out of the hole.** Don't bill hourly — the hours are Claude sessions and
hourly caps us at cost-plus on the one project where cost basis is meaningless. **Never show Caleb
section 1** — it is 1% of replacement cost and it would end the negotiation. Don't transfer the
repo before terms are signed. Don't keep building while blocked on his deliverables (telemetry
radio, receiver, Stripe live keys, the pump-sensing answer that gates spray verification).

Get liability language written before first flight: software as-is, operator responsible for
airworthiness, Part 137, chemical licensing and airspace. This is a 60-lb aircraft dispensing
chemicals over other people's land — that clause is not boilerplate.

---

## 6. Open items

Everything the numbers depend on that isn't settled. Short list on purpose.

| Item | Owner | Note |
|---|---|---|
| **No agreement with Caleb exists.** Nothing invoiced since 2026-08-14. | Tabor | This is the only item that actually matters. The arithmetic is done; the conversation is not. Every session that ships work without it widens the gap. |
| Recover the 2026-08-15/16 cost row | Tabor | Currently a ~$120 estimate. If the previous laptop is ever accessible, run `py tools\session_cost.py --since 2026-08-15` **on that machine** and replace the row with the measurement. Otherwise it stays an estimate forever. |
| ~~Top up the last ledger row~~ **DONE 2026-08-19** | — | `69aaf91a` was $16.49, actually $18.17; the +$1.68 delta is its own row. Two rows from 2026-08-19 now need the same treatment (footnote ³). |
| **Parallel sessions break the labour method** | whoever appends next | Four sessions overlapped on 2026-08-19. Summing their wall clock would have tripled the day. Union overlapping spans — see the warning in section 2. |
| Labour hours before 2026-08-18 are estimated | Tabor | ~55–75 h, unmeasurable for the same profile reason. Only worth revisiting if the old laptop resurfaces; the working figure of 90 h total is conservative either way. |
| First real flight, spray hardware layer | Caleb (hardware) / us (software) | Both are section 5 milestones worth $15k–50k each to Frame B. Neither is scheduled. |
| `spreader-project` has no ledger | Tabor | Same partner, same unpaid pattern, and it has a commercial target (USC LLC supply agreement). If it goes anywhere, it needs its own `VALUATION.md` — `tools\session_cost.py` works there with `PROJECT_MARKER` changed. |

---

## 7. How to update this file

At the end of any session that did work on this project:

```
cd C:\Users\jacks\rc-plane-app        # or wherever the repo lives on this machine
py tools\session_cost.py --new        # rows not already in this file
```

Then:

1. **Append** the printed rows to section 1 (period, one-line description, session id, cost).
   Append — never rewrite. Old rows are the record.
2. **Append** the hours to section 2 and update the working total.
   Also correct the previous session's row with `--session <id>` (footnote 2 in section 1).
3. If any *Inputs* in section 3 changed materially (say, +2,000 lines or a new subsystem),
   re-run those commands and recompute Frame A.
4. If a section 4 milestone landed, move Frames B and C and say so in the row.
5. Update *Last reconciled* at the top, and section 6 if an open item moved. Commit and push — that is what makes it reachable
   from the next machine.

The script only sees transcripts on the **current** machine. `--min-share` (default 0.5) decides
how much of a session must have been in this repo to count; sessions below it are printed
separately as MIXED so a Relevyn evening never gets billed to Caleb by accident. Sessions from a
retired machine are invisible — that is why section 1 row three is an estimate, and why the
procedure is *append*, not *regenerate*.
