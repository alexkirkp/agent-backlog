# agent-backlog

**A token-efficient, git-backed Markdown task queue (`BACKLOG.md` + `COMPLETED.md`) and linter for concurrent AI coding agents.**

Works out of the box with **Claude Code**, **OpenAI Codex**, **Gemini CLI / Antigravity**, **Cursor**, **Windsurf**, and **Aider**—even when multiple sessions edit the same working tree in parallel.

---

## Built for Token Efficiency

A naive `TODO.md` or external issue tracker burns tokens fast as a project grows. `agent-backlog` keeps per-session context minimal even after hundreds of completed tasks:

* **Read only what's needed:** Agents read the lean summary table and the single entry they are working on—never the whole file.
* **Hard 45-line cap per task:** Active entries stay short and session-sized (`tools/check_backlog.py` enforces `--max-lines 45`). Deep research and shared architecture notes live in `spikes/` and are loaded only when relevant.
* **Zero-token history:** Finished tasks move out of `BACKLOG.md` into `COMPLETED.md`. Because `python tools/check_backlog.py` prints the next free ID in each tier and the next `§N` milestone number, agents allocate IDs and append completions **without ever reading `COMPLETED.md`**.
* **Zero MCP or schema overhead:** Plain Markdown at the repo root means no extra MCP tool definitions in your system prompt and no multi-turn API calls to sync with Jira, Linear, or GitHub Issues.
* **Model-tier routing:** Every task carries a **Model Fit** tag (`Planner`, `Frontier`, `Workhorse`, `Fast`, `Human`) so you don't spend frontier-model tokens on mechanical work.

---

## What It Looks Like

At the top of `BACKLOG.md`, the **summary table** is both the priority-ordered work queue and a compact navigation index:

```markdown
| ID | Task Name | Focus Area | Status | Model Fit | Target Surface / Script |
|:---:|---|---|:---:|:---:|---|
| **B0.1** | [Run the Backlog Checker in CI](#b01-run-the-backlog-checker-in-ci) | Repository Hygiene | 📋 Planned | **Workhorse** | `.github/workflows/`, `tools/check_backlog.py` |
| **B1.1a** | [Give the Shared HTTP Client Timeouts and Retries](#b11a-give-the-shared-http-client-timeouts-and-retries) | Reliability | 🚧 In Progress | **Workhorse** | `src/http_client.*`, its tests |
| **B1.1b** | [Route Every Outbound HTTP Call Through the Shared Client](#b11b-route-every-outbound-http-call-through-the-shared-client) | Reliability | 📋 Planned | **Fast** | call sites that build requests directly |
```

Each task has a **session-sized entry** focused on *why* it matters and *what "done" looks like* (rather than brittle line-by-line instructions that go stale):

```markdown
### B1.1a. Give the Shared HTTP Client Timeouts and Retries
* **Status:** 🚧 **In Progress** (chunk 1 of 2 of B1.1)
* **Model Fit:** Workhorse (a contained change to one module with careful tests)
* **Why it matters:** outbound calls hang when upstream stalls, and a single 429 or 503 surfaces as a hard failure.
* **End goal:** every request has a default timeout. Idempotent requests retry with backoff on 429/5xx and honor `Retry-After`.
* **Automated Verification:** tests against a stubbed transport covering timeouts, 503 retries, `Retry-After`, and non-idempotent POSTs.
```

---

## How Parallel Agents Stay Out of Each Other's Way

1. **Claim in place:** Starting "the next task" means taking the top `📋 Planned` row and immediately flipping it to `🚧 In Progress` on disk so concurrent sessions skip to the next row.
2. **Split large work into lettered chunks:** Multi-session features become `B1.1a`, `B1.1b`, … ordered by dependency in the table, sharing a single design doc in `spikes/B1.1_short_slug.md`.
3. **Close and verify atomically:** In the same session that finishes the code, the agent moves the entry to `COMPLETED.md`, runs `python tools/check_backlog.py`, and commits both files with the code.

---

## Quickstart

1. **Copy into your repository root:**
   ```text
   BACKLOG.md                  Active task queue: conventions, model routing guide, summary table, entries
   COMPLETED.md                Append-only ledger of delivered work (§1, §2, …)
   spikes/                     On-demand architecture & research notes
   tools/check_backlog.py      Zero-dependency linter (Python 3.8+, stdlib only)
   ```
2. **Add agent instructions:** Paste `AGENTS.snippet.md` into your `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, or `.cursor/rules/*.mdc`.
3. **Validate (and get next free IDs):**
   ```sh
   $ python tools/check_backlog.py
   backlog OK: 3 entries (next IDs: B0.2, B1.2, B2.1, B3.1; next completed: §1)
   ```

`tools/check_backlog.py` exits `1` on table-to-body drift, entries over 45 non-blank lines, broken anchors or `spikes/` links, duplicate/retired IDs, or out-of-order `§N` milestones in `COMPLETED.md`. (Adopting on an existing ledger? Add `<!-- backlog-check: legacy-through=N -->` to `COMPLETED.md` to exempt milestones `§N` and earlier.)
