# Development Backlog

This document tracks active development tasks, research spikes and planned enhancements for this
project. It is strictly organized by priority:

* **Tier 0 (Repository Hygiene & Blockers):** Work that keeps the repository healthy or unblocks
  everything else: CI, security, privacy boundaries, broken builds.
* **Tier 1 (High Priority — Immediate Day-to-Day Impact):** Bugs and features users or the team hit
  every day.
* **Tier 2 (Medium Priority — Workflow & Depth):** Improvements that matter but can wait a cycle.
* **Tier 3 (Low Priority / Backlog — Specialized & Speculative):** Niche work, spikes and ideas.
* **Archived / Completed:** Delivered work moves to the permanent ledger in
  [`COMPLETED.md`](COMPLETED.md).

---

## Backlog Authoring Conventions

* **State purpose, end goal, and background up front.** Every entry should make clear *why* it
  exists (motivation, provenance, the problem it solves) and *what "done" looks like*: enough
  for a future reader to evaluate it without reconstructing context from chat history.
* **Describe the "what" and "why"; keep the "how" light.** Avoid locking entries into a specific
  implementation. Exact function signatures, library calls or step-by-step blueprints read as
  already-decided architecture and go stale the moment the codebase moves. A rough sketch of
  approach is fine for scoping; flag it as illustrative, not prescriptive.
* **Tier reflects current priority, not history.** Highest-impact/daily-use work belongs in
  Tier 1, occasional/niche work toward Tier 3. When priority changes, move the entry and
  renumber its ID to match its new tier, keeping the old ID in the status line for provenance
  (e.g. `(formerly B3.4)`).
* **IDs are `B<tier>.<n>` and never reused.** Take the next free number in the tier, counting
  IDs already in [`COMPLETED.md`](COMPLETED.md) (running `python tools/check_backlog.py` prints
  the next free ID in each tier and the next `§N` milestone number). Lettered chunks (below) add
  a suffix: `B1.1a`, `B1.1b`.
* **The summary table is the work queue: top row first.** The backlog is worked top-down, so row
  order is the execution order. Order by priority, then by dependency: an entry that unblocks or
  must land before another sits above it (if that crosses a tier, re-tier the blocker upward).
  Place new rows where their impact and dependencies put them, never appended at the bottom by
  default, and re-check the order whenever you add, re-tier or finish an entry. Body sections
  follow the table's order.
* **Keep the summary table lean.** Table rows are a fast navigation index (ID, title, focus area,
  status, model fit, target surface); put the "why" and any implementation notes in the body
  section below, not the table.
* **Scope each item to a single working session.** An entry should be sized so it can be
  researched, implemented and verified in one pass. If a blueprint spans multiple unrelated
  files or subsystems that don't need to land together, split it into separate IDs/tier slots
  rather than one oversized entry.
* **Work bigger than one session becomes lettered chunks.** It keeps one number, and each
  chunk takes a letter: `B1.1a`, `B1.1b`, …. Each chunk gets its own row and entry, is sized
  to one session, and is ordered by dependency in the table. Background the chunks share goes
  in `spikes/<id>_<slug>.md` instead of being repeated in each entry. Chunks move to
  `COMPLETED.md` one at a time as they land. When the last one lands, add an umbrella closure
  entry for the parent ID that maps each chunk to its §. Re-tier the chunks together, keeping
  their letters.
* **Keep entry bodies under 45 non-blank lines** (`tools/check_backlog.py`). An entry is read
  whole by every session that touches it; put design notes, lessons and long approach sketches
  in `spikes/<id>_<short_slug>.md` and link them from the entry.
* **Completed items move to `COMPLETED.md`.** When a task is delivered, record its milestone
  entry in [`COMPLETED.md`](COMPLETED.md) and remove it from `BACKLOG.md` entirely. Do not leave
  historical summaries, changelog entries or completion blurbs behind in `BACKLOG.md`.

### Entry template

```markdown
### B2.7. Short Imperative Title
* **Status:** 📋 **Planned** (YYYY-MM-DD: where the item came from)
* **Model Fit:** <tier> (<one-line reason>)
* **Why it matters:** the problem, who hits it, and the evidence.
* **End goal:** what "done" looks like, observable from outside the code.
* **Background:** links to spikes, issues, related entries.
* **Automated Verification:** the tests or checks that prove it.
```

Optional fields when they earn their place: **Approach (illustrative)**, **Guardrail** (what must
not change until something is proven), **Depends on**, **Open questions**.

### Status legend

| Glyph | Status | Meaning |
|:---:|---|---|
| 📋 | Planned | Scoped and ready to pick up. |
| 🚧 | In Progress | Someone (human or agent) is working on it now. |
| 💡 | Conceptual | An idea that needs a spike before it can be scoped. |
| ⏸️ | Gated (Ask First) | Blocked on a decision or approval; don't start without asking. |
| ✅ | Completed | Used only in `COMPLETED.md`. |

---

## Model Selection & Routing Guide

Use this reference to route tasks by capability tier rather than a specific vendor's model name.
Map these tiers to whichever models and agents your team runs.

* **Planner (planning and architecture model):** Planning major or complicated
  features before anyone writes code: research, design across subsystems, and splitting the
  work into lettered, session-sized chunks with their dependencies. The output is a spike in
  `spikes/` and the chunk entries in this file, which other models then implement. Also suited
  to broad architecture reviews and cross-cutting audits.
* **Frontier (deep reasoning model):** Novel algorithms, the hardest single chunks
  of a planned feature, hard debugging where the cause is unknown.
* **Workhorse (standard coding model):** The primary driver for feature implementation,
  refactoring, API clients, frontend work and test-driven development.
* **Fast (lightweight, low-cost model):** Mechanical multi-file renames and path updates,
  fixture authoring, high-volume automation.
* **Human:** Anything needing hardware, accounts, credentials, judgment calls on product
  direction, or a test on a machine the agents can't reach. Write it as `Human + <tier>` when
  both are needed.

---

## Active Engineering Backlog

| ID | Task Name | Focus Area | Status | Model Fit | Target Surface / Script |
|:---:|---|---|:---:|:---:|---|
| **B0.1** | [Run the Backlog Checker in CI](#b01-run-the-backlog-checker-in-ci) | Repository Hygiene | 📋 Planned | **Workhorse** | `.github/workflows/`, `tools/check_backlog.py` |
| **B1.1a** | [Give the Shared HTTP Client Timeouts and Retries](#b11a-give-the-shared-http-client-timeouts-and-retries) | Reliability | 📋 Planned | **Workhorse** | `src/http_client.*`, its tests |
| **B1.1b** | [Route Every Outbound HTTP Call Through the Shared Client](#b11b-route-every-outbound-http-call-through-the-shared-client) | Reliability | 📋 Planned | **Fast** | call sites that build requests directly |

---

Completed milestones have moved out of this file entirely. The full index and write-ups live in
[`COMPLETED.md`](COMPLETED.md).

---

## Tier 0: Repository Hygiene & Blockers

---

### B0.1. Run the Backlog Checker in CI
* **Status:** 📋 **Planned** (example entry: delete or replace it once you've read it)
* **Model Fit:** Workhorse (a small workflow file and one verification run)
* **Why it matters:** the conventions above only hold if something enforces them. Agents editing
  this file in parallel will drift: a table row whose body was deleted, a body over the length
  cap, an entry left under the wrong tier after a re-prioritization, an ID that also appears in
  `COMPLETED.md`. `tools/check_backlog.py` catches all of these, but only when someone runs it.
* **End goal:** every push and pull request that touches `BACKLOG.md` or `COMPLETED.md` runs the checker, and a
  failing check blocks the merge. Contributors can run the same command locally.
* **Approach (illustrative):** a GitHub Actions job on `ubuntu-latest` with `actions/setup-python`
  that runs `python tools/check_backlog.py`. No dependencies beyond the standard library.
* **Automated Verification:** a deliberately broken branch (an entry body over 45 lines) fails the
  job; reverting it passes.

---

## Tier 1: High Priority — Immediate Day-to-Day Impact

---

### B1.1a. Give the Shared HTTP Client Timeouts and Retries
* **Status:** 📋 **Planned** (example entry, chunk 1 of 2 of B1.1: a lettered set split from one
  task too big for a session)
* **Model Fit:** Workhorse (a contained change to one module with careful tests)
* **Why it matters:** outbound calls hang for minutes when an upstream service stalls, and a
  single 429 or 503 surfaces to users as a hard failure. The project has a shared HTTP client
  module, but it has no default timeout and doesn't retry, so moving callers onto it (B1.1b)
  would gain nothing yet.
* **End goal:** every request through the shared client has a default timeout. Idempotent
  requests retry with backoff on connection errors, 429 and 5xx, and honor `Retry-After`.
  Non-idempotent requests never retry silently. Callers can override each of these per call.
* **Automated Verification:** tests against a stubbed transport: a stall hits the timeout, a 503
  then 200 succeeds on the retry, a 429 waits out `Retry-After`, a POST is not retried, and the
  retry limit is respected.

---

### B1.1b. Route Every Outbound HTTP Call Through the Shared Client
* **Status:** 📋 **Planned** (example entry, chunk 2 of 2 of B1.1)
* **Model Fit:** Fast (mechanical call-site swaps once B1.1a has set the behavior)
* **Depends on:** B1.1a. Moving callers before the client retries would only spread its gaps.
* **Why it matters:** code written before the shared client existed builds requests directly,
  with its own ad hoc timeouts or none. Each of those is a place where a fix to the client
  (a retry policy, an auth header, logging) doesn't reach.
* **End goal:** no module outside the shared client builds HTTP requests itself. Where a swap
  changes behavior (a caller gains retries, or a call that hung now times out), the change is
  noted in the completion write-up.
* **Automated Verification:** a test that scans the source tree and fails if the HTTP library is
  imported anywhere but the shared client; the full suite passes after the swap.

---

## Tier 2: Medium Priority — Workflow & Depth

---

## Tier 3: Low Priority / Backlog — Specialized & Speculative
