<!--
Paste the section below into your project's agent instruction file: AGENTS.md, CLAUDE.md,
GEMINI.md, .cursor/rules/*.mdc or similar. Adjust the paths if you keep the backlog files somewhere
other than the repository root. The "Shared working tree" bullets matter only when several agents or people
edit the same checkout at once; drop them otherwise.
-->

## Development Backlog

* **Where it lives:** the active backlog is `BACKLOG.md`; delivered work is
  recorded in `COMPLETED.md`, and long design notes in `spikes/`. Consult the
  backlog directly for tasks and roadmap items without searching for it.
* **Read only what you need.** Read the summary table and the one entry you're working on, not
  the whole file. `COMPLETED.md` grows without bound; search it by ID instead of reading it.
* **Work top-down.** The summary table is the work queue: when asked for "the next task", take
  the top row whose status is 📋 Planned. Don't start a ⏸️ Gated item without asking.
* **Keep `BACKLOG.md` current.** When a task you're working on finishes (a backlog item,
  a bug fix, a milestone), update the backlog in the same session: write the entry into
  `COMPLETED.md` (next § number, plus an index line) and remove it from `BACKLOG.md`. Flip a row to
  🚧 In Progress when you start one. Don't leave completed work marked planned or in-progress
  for someone else to reconcile later.
* **New entries follow the authoring conventions** at the top of `BACKLOG.md`: why and done-state
  up front, single-session scope, body under the line cap, row placed by priority and
  dependency, not appended at the bottom.
* **Check before you finish (and to get the next free IDs):** `python tools/check_backlog.py`
  prints the next available `BN.x` ID per tier and the next `§N` milestone number (so you don't
  need to scan `COMPLETED.md` to allocate an ID), and must pass after any backlog edit.
* **Shared working tree:** other agents and people may edit the backlog at the same time. A row
  that appeared or flipped status mid-session is a teammate's work, not corruption. Make small,
  targeted edits; if an edit fails because the text moved, re-read the file and apply your
  change on top.
* **Commit the backlog whole.** Whenever you've touched `BACKLOG.md` or `COMPLETED.md`, stage and
  commit the entire file, including others' uncommitted rows (an item moved from BACKLOG to
  COMPLETED must land in both), and say in one line that the commit carried others' backlog
  edits.
* **Status glyphs in shell heredocs:** some agent shells truncate commands that contain emoji.
  Edit the backlog with your file-editing tool rather than through a shell heredoc.
