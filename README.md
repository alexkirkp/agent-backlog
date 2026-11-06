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
2. **Add agent instructions:** Paste `AGENTS.snippet.md` into your `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, or `.cursor/rules/*.mdc`. Antigravity (`agy`) and Cursor read `AGENTS.md`, and agy ignores `CLAUDE.md`; to serve all three, put the snippet in `AGENTS.md` and have `CLAUDE.md` say `@AGENTS.md`.
3. **Validate (and get next free IDs):**
   ```sh
   $ python tools/check_backlog.py
   backlog OK: 3 entries (next IDs: B0.2, B1.2, B2.1, B3.1; next completed: §1)
   ```

### Working the queue unattended (optional)

`tools/batch_backlog.py` runs the `📋 Planned` rows one at a time, top to bottom, each in a fresh headless `claude -p`, `agy -p` or Cursor `agent -p` session, so nothing piles up in one long chat. Each session works its task as far as it can, choosing sensible defaults instead of stopping to ask. A finished task is tested, moved to `COMPLETED.md` and committed. When pieces genuinely need a human, the session commits what it finished, flips the row to `⏸️ Gated` and lists what's left as **Open questions**. Rows whose Model Fit names a human, whose `Depends on:` isn't done, or whose target files have uncommitted edits are skipped.

```sh
python tools/batch_backlog.py --dry-run                      # preview the queue and the prompt
python tools/batch_backlog.py --limit 3 --tier-model workhorse=sonnet --tier-model frontier=opus
python tools/batch_backlog.py --instructions "Don't touch the public API."   # append to every item's prompt
python tools/batch_backlog.py --instructions-file batch_notes.md           # same, for multi-line notes
python tools/batch_backlog.py --status --wait                # monitor; blocks until the running item changes
```

Run logs land in `.backlog_batch/` (gitignored). Needs the `claude`, `agy` or Cursor `agent` CLI on PATH; the script itself is stdlib-only. Tiers route to models via `--tier-model` or a routing-guide line like `* **Workhorse** (Claude Sonnet 5): ...`; `--model` overrides both.

**With Antigravity:** pass `--agent agy` (or set `BACKLOG_AGENT=agy`; without either, the runner uses `claude` when it's on PATH, else `agy`). Guide names such as `Gemini 3.8 Flash` or `Claude Opus 5.5` resolve against `agy models` (the `-medium` variant by default), and `--tier-model fast=gemini-3.8-flash-high` takes any id it lists. One setup step first: headless agy can't ask for permission, and its allow-rules match whole commands only, so add this to `~/.gemini/antigravity-cli/settings.json`:

```json
{ "permissions": { "allow": ["command(*)"] } }
```

That lets every agy CLI session run shell commands without asking, interactive ones included. If you'd rather not, pass `--permission-mode skip` per batch (agy's `--dangerously-skip-permissions`). Without either, the first item stops with an error naming both fixes. agy reports tokens rather than dollars, and its conversations can't be read live, so `--status` shows the tree and commits for the running item but not its tool calls, and a session ends on its own or at `--timeout-min`. Reopen one with `agy --conversation <id>`.

**With Cursor:** pass `--agent cursor` (or `BACKLOG_AGENT=cursor`). It needs Cursor's CLI (`agent`, from `irm 'https://cursor.com/install?win32=true' | iex` or `curl https://cursor.com/install -fsS | bash`) logged in or `CURSOR_API_KEY` set. Each item runs `agent -p --force --trust --output-format stream-json`: print mode only *proposes* edits without `--force`, and any `deny` rules in `~/.cursor/cli-config.json` or `.cursor/cli.json` still hold. Guide names resolve against `agent models` (`Claude Opus 5.5` → `claude-opus-5-5`), and the event stream lets `--status` show the running item's tool calls. Cursor reports tokens, not dollars. Reopen a session with `agent --resume <id>`.

*Pinned models or Auto?* Cursor's `auto` model hands each request to its router. On individual plans that's Auto's **Cost** mode only, which optimizes for spend and doesn't say which model ran; the quality-seeking **Balance** and **Intelligence** modes are Teams/Enterprise, set by an admin, not per run. So the runner pins by default and Auto is opt-in per tier. Auto suits the tiers where cheap is the point and pins suit the ones where a weaker model costs you a redo:

```sh
python tools/batch_backlog.py --agent cursor --tier-model fast=auto --tier-model workhorse=auto
```

Frontier and Planner stay on the guide's named models. On a Teams plan with Router set to Intelligence, `--model auto` for everything is reasonable too.

`tools/check_backlog.py` exits `1` on table-to-body drift, entries over 45 non-blank lines, broken anchors or `spikes/` links, duplicate/retired IDs, or out-of-order `§N` milestones in `COMPLETED.md`. (Adopting on an existing ledger? Add `<!-- backlog-check: legacy-through=N -->` to `COMPLETED.md` to exempt milestones `§N` and earlier.)
