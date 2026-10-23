# agent-backlog

**A zero-dependency, git-backed Markdown task queue (`BACKLOG.md` + `COMPLETED.md`) and Python linter designed for concurrent AI coding agents and multi-agent workflows.**

Works out of the box with **Claude Code** (`CLAUDE.md`), **OpenAI Codex** (`AGENTS.md`), **Gemini CLI / Antigravity** (`GEMINI.md`), **Cursor** (`.cursor/rules`), **Windsurf**, **Aider**, and human developers—even when multiple parallel sessions edit the same working tree simultaneously.

* **Zero external state:** No Jira, Linear, MCP server, or SQLite database to sync. Tasks travel with your git branches and diffs.
* **Context-window friendly:** Active tasks stay short (capped at 45 non-blank lines), long architecture notes live in `spikes/`, and finished work moves to an append-only ledger (`COMPLETED.md`).
* **Collision-resistant:** Agents claim tasks in place and read only the summary table plus their assigned entry.
* **Linter-enforced:** A fast, zero-dependency Python 3.8+ script (`tools/check_backlog.py`) catches drift, broken links, duplicate IDs, and ordering errors in CI or pre-commit hooks.

---

## Why a plain-Markdown backlog for AI agents?

When you run multiple AI coding agents in parallel sessions against a single repository, a naive `TODO.md` falls apart quickly:

| Common multi-agent failure mode | How `agent-backlog` solves it |
|---|---|
| **Two agents pick up the same task** | The summary table is a strict top-down work queue. Starting a task means flipping the top `📋 Planned` row to `🚧 In Progress` immediately so the next session skips it. |
| **Token bloat from reading the whole backlog** | Agents read only the summary table and the single entry they are working on. Bodies are capped at 45 lines; deep context lives in `spikes/`; completed tasks leave `BACKLOG.md` entirely. |
| **Stale implementation blueprints** | Entries specify *why* the problem matters and *what "done" looks like* (`End goal` + `Automated Verification`), not fragile line-by-line instructions that rot as the codebase moves. |
| **Merge conflicts and formatting drift** | Parallel edits inevitably drift. `tools/check_backlog.py` validates table-to-body sync, anchor slugs, tier numbering, and retired IDs, and prints the next free ID per tier so agents never have to scan `COMPLETED.md`. |
| **Wasting frontier models on mechanical tasks** | Every entry carries a **Model Fit** tag (`Planner`, `Frontier`, `Workhorse`, `Fast`, `Human`) so you or an orchestrator can route each task to the right model tier. |

This template came out of a long-running production codebase where several agents ran in parallel against one backlog every day. Hundreds of tasks moved through it without collisions or context bloat.

---

## What It Looks Like

At the top of `BACKLOG.md`, the **summary table** acts as the live work queue and navigation index:

```markdown
| ID | Task Name | Focus Area | Status | Model Fit | Target Surface / Script |
|:---:|---|---|:---:|:---:|---|
| **T0.1** | [Run the Backlog Checker in CI](#t01-run-the-backlog-checker-in-ci) | Repository Hygiene | 📋 Planned | **Workhorse** | `.github/workflows/`, `tools/check_backlog.py` |
| **T1.1a** | [Give the Shared HTTP Client Timeouts and Retries](#t11a-give-the-shared-http-client-timeouts-and-retries) | Reliability | 🚧 In Progress | **Workhorse** | `src/http_client.*`, its tests |
| **T1.1b** | [Route Every Outbound HTTP Call Through the Shared Client](#t11b-route-every-outbound-http-call-through-the-shared-client) | Reliability | 📋 Planned | **Fast** | call sites that build requests directly |
```

Below the table, each task has a **session-sized entry** focused on motivation, observable outcomes, and verification:

```markdown
### T1.1a. Give the Shared HTTP Client Timeouts and Retries
* **Status:** 🚧 **In Progress** (chunk 1 of 2 of T1.1)
* **Model Fit:** Workhorse (a contained change to one module with careful tests)
* **Why it matters:** outbound calls hang when upstream stalls, and a single 429 or 503 surfaces as a hard failure.
* **End goal:** every request has a default timeout. Idempotent requests retry with backoff on 429/5xx and honor `Retry-After`.
* **Automated Verification:** tests against a stubbed transport covering timeouts, 503 retries, `Retry-After`, and non-idempotent POSTs.
```

When `T1.1a` ships, the agent removes it from `BACKLOG.md` and appends a permanent milestone record (`§1, §2, …`) to `COMPLETED.md`.

---

## Repository Layout

The backlog files sit at the repository root—the first place an LLM agent or developer looks when inspecting a project:

```text
BACKLOG.md                  Active task queue: conventions, model routing guide, summary table, tiered entries
COMPLETED.md                Append-only changelog & ledger of delivered work, numbered §1, §2, …
spikes/                     Long architecture & research notes (one file per task that needs one)
AGENTS.snippet.md           Drop-in instructions for AGENTS.md, CLAUDE.md, GEMINI.md, or Cursor rules
tools/
  check_backlog.py          Zero-dependency convention linter (Python 3.8+, standard library only)
  test_check_backlog.py     Unit test suite for the linter
.github/workflows/
  backlog.yml               GitHub Actions CI workflow running the checker and test suite
```

---

## Quickstart: Add to Your Project in 2 Minutes

1. **Copy the files** into the root of your repository:
   * `BACKLOG.md`, `COMPLETED.md`, `spikes/`, `tools/check_backlog.py`
   * *(Optional)* `.github/workflows/backlog.yml` for GitHub Actions CI
2. **Wire up your agent instructions:** Paste the contents of `AGENTS.snippet.md` into your project's `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, or `.cursor/rules/*.mdc`.
3. **Customize `BACKLOG.md`:** Adjust the tier descriptions and model routing guide for your stack, then replace the example entries (`T0.1`, `T1.1a`, `T1.1b`) with your own tasks.
4. **Verify with the checker:**

   ```sh
   python tools/check_backlog.py
   ```

   When everything is valid, the checker outputs the next available IDs so agents can allocate new tasks without reading `COMPLETED.md`:

   ```text
   backlog OK: 3 entries (next IDs: T0.2, T1.2, T2.1, T3.1; next completed: §1)
   ```

---

## How the Multi-Agent Workflow Operates

Once `AGENTS.snippet.md` is in your system prompt or instruction file, you can prompt any coding agent naturally:

* *"Pick up the next backlog item."*
* *"Add a Tier 1 backlog entry for fixing the auth token race condition."*
* *"Spike the database migration in `spikes/` and break it into lettered backlog chunks."*

Behind the scenes, the rules keep concurrent sessions from colliding:

1. **Claim before working:** The agent flips the top `📋 Planned` row to `🚧 In Progress` on disk before writing code, signaling other agents in the same checkout to take the next row down.
2. **Read minimally:** The agent reads the summary table and its target entry—never the entire `BACKLOG.md` or `COMPLETED.md`.
3. **Coexist in a shared working tree:** Agents make small, targeted file edits and treat rows that appear or change status mid-session as a teammate's live work.
4. **Close and commit atomically:** In the same session that finishes the code, the agent moves the entry from `BACKLOG.md` to `COMPLETED.md`, runs `python tools/check_backlog.py`, and commits both backlog files together.

---

## What `check_backlog.py` Enforces

`tools/check_backlog.py` exits `1` on any violation, making it ideal as a pre-commit hook, agent verification step, or CI gate:

* **Queue & ordering sync:** Every summary-table row has a matching body entry, every body entry has a row, and body entries appear in the exact same order as the summary table.
* **Session-sized scope:** Entry bodies stay under `--max-lines` (default `45` non-blank lines).
* **Tier & ID integrity:** Entries sit under the `## Tier N` section matching their `TN.x` ID. Duplicate IDs, retired `(formerly TN.x)` IDs, and IDs already closed in `COMPLETED.md` are rejected.
* **Status consistency:** Every entry has a `**Status:**` line whose emoji glyph (`📋`, `🚧`, `💡`, `⏸️`) matches its summary-table row. Items marked `✅ Completed` in `BACKLOG.md` fail the check until moved to `COMPLETED.md`.
* **Link & ledger validation:** Summary-table anchors match their Markdown headings, `spikes/...` links point to existing files, and `COMPLETED.md` maintains sequential `## N.` milestones with matching `[§N](#...)` index links. Each number must follow the one before it, so when two sessions take the same next `§N`, the collision is reported once rather than shifting every later milestone.
* **Adopting it with an existing ledger:** if your `COMPLETED.md` predates the checker, add `<!-- backlog-check: legacy-through=N -->` to it. Milestones `§N` and earlier are exempt from the ledger rules (numbering, heading ID, index line, double closure) but their IDs still count as closed, so history stays put and everything after `§N` is checked in full.

---

## Frequently Asked Questions

### Why not use GitHub Issues, Linear, or an MCP task server?
External trackers are great for product management, but they live outside the git working tree. In a fast-moving multi-agent repo, keeping an external tracker in sync with local branches, uncommitted work, and parallel agent sessions requires API tokens, network calls, and extra tool definitions. A plain Markdown file at the repo root is readable by every CLI and IDE agent with zero setup, works offline, and commits atomically alongside the code that resolves it.

### How do you handle tasks that are too big for a single agent session?
Large features are broken into **lettered chunks** (`T1.1a`, `T1.1b`, `T1.1c`). Shared architecture notes go into `spikes/T1.1_short_slug.md`, while each lettered chunk gets its own row in the summary table, is ordered by dependency, and is scoped to land in a single session.

### Doesn't `COMPLETED.md` eventually get huge?
Yes—by design. Because agents are instructed never to read `COMPLETED.md` in full (they only append to the bottom and use `python tools/check_backlog.py` to discover the next free `TN.x` ID and `§N` milestone number), `COMPLETED.md` can grow to hundreds of milestones without costing a single token during normal task execution, while remaining searchable via `git grep` or ripgrep when investigating historical context.
