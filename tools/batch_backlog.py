#!/usr/bin/env python3
"""Work BACKLOG.md's 📋 Planned rows unattended, one fresh headless agent session each.

Each item runs as its own `claude -p` (Claude Code) or `agy -p` (Antigravity) session, so no context
carries over between items and nothing piles up in one long chat. `--agent` picks the CLI; without
it, `$BACKLOG_AGENT`, else `claude` when it is on PATH, else `agy`. Items run one at a time, top to bottom in the summary table,
because they share the working tree. BACKLOG.md is re-read before each pick, so a row that an
earlier item finished, blocked or reordered is seen as it is now.

Every session gets the same brief: work the item as far as it can (tests green, moved to
COMPLETED.md, committed), and when pieces need a human, commit what's done, flip the row to ⏸️ and
list what's left as **Open questions**. The
reply ends in a `BATCH_RESULT:` line, which goes into a markdown run log under
`.backlog_batch/` with the commits, cost and session id (`claude --resume <id>` or
`agy --conversation <id>` reopens a session). The raw JSON of each session sits next to the log.

A session that sits quiet for `--idle-min` after its `BATCH_RESULT:` line is ended and that line is
its result (a background Monitor can keep a finished `claude -p` alive). A session that dies or is
ended without a result line but left a commit with the task ID is recorded done, recovered from git.

The batch stops early when the CLI errors or times out (usage limit, auth, a hung run), so a bad
night leaves at most one item half done. Rows whose Model Fit names a human are skipped unless
`--include-human`.

Each item runs on the model its Model Fit tier maps to. The map comes from the routing guide when a
tier line names a Claude model in brackets (`* **Workhorse** (Claude Sonnet 5): ...`, the first one
wins, used as a family alias) and from `--tier-model TIER=ALIAS`, which wins over the guide. A tier
with no model runs on your Claude Code default; `--model` overrides every item.

`--status --wait` blocks until the running item changes or the batch ends, then prints the same view,
so a monitor needs no fixed-interval polling. The running item's cost so far is estimated from its
transcript's token usage (list prices in `PRICE_PER_MTOK`; finished items carry the CLI's own figure).

`--status` is the monitoring view: one screen of facts about the latest batch (or the stamp
given), built from its `state.json` and the running session's live transcript. It reports what
is happening (idle time, recent tool calls, errors, permission denials, repeated calls, files
left uncommitted, the backlog check, the queue) and leaves the call on whether it's healthy to
whoever reads it.

With `--agent agy` the differences are: the routing guide's model names (Gemini or Claude) resolve
against `agy models`; agy reports tokens, not cost; its conversations are not readable live, so the
idle-after-result check and the transcript part of `--status` are Claude-only (agy sessions end on
their own or at `--timeout-min`). agy cannot ask for permission headless: give it `command(*)` under
`permissions.allow` in `~/.gemini/antigravity-cli/settings.json` (its rules match whole commands
only, so narrower rules don't cover a batch), or pass `--permission-mode skip`. A session that hits a
denial is an error and stops the batch.

Usage: python tools/batch_backlog.py [--agent claude|agy] [--dry-run] [--limit N] [--only B1.1,B1.2]
       [--model M] [--tier-model workhorse=sonnet] [--permission-mode auto] [--max-budget-usd X]
       [--timeout-min N] [--instructions TEXT] [--instructions-file PATH]
       python tools/batch_backlog.py --status [STAMP] [--wait]

Standard library only, Python 3.8+ (plus the `claude` or `agy` CLI on PATH). Run it from the repo root.
"""

import argparse
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TOOLS_DIR)
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from check_backlog import check, parse_backlog  # noqa: E402

log = logging.getLogger(__name__)

DEFAULT_BACKLOG = os.path.join(PROJECT_ROOT, "BACKLOG.md")
LOG_ROOT = os.path.join(PROJECT_ROOT, ".backlog_batch")
MAX_LINES = 45  # check_backlog's entry cap, for the status view's backlog check
STATE_FILE = "state.json"
AGENTS = ("claude", "agy")
AGY_SETTINGS = "~/.gemini/antigravity-cli/settings.json"
AGY_EFFORT = "medium"  # agy model ids carry an effort suffix; a guide name without one gets this
MODEL_FAMILIES = ("fable", "opus", "sonnet", "haiku")
GUIDE_TIER = re.compile(r"^\* \*\*([A-Za-z]+)\*\* \(([^)]*)\)")
DENIAL_RE = re.compile(r"permission|denied|not allowed", re.IGNORECASE)
RESULT_PREFIX = "BATCH_RESULT:"
RESULTS = ("done", "partial", "blocked", "failed")
RECENT_CALLS = 20
TASK_ID = re.compile(r"\bB\d+\.\d+[a-z]?\b")
TARGET_FILE = re.compile(r"`([^`\s]+\.[A-Za-z0-9]+)`")
# USD per million tokens (input, output) by model family. An estimate for the running item only; the
# CLI's own total replaces it once the item finishes. Cache reads cost 0.1x input, writes 1.25x (5 min) or 2x (1 h).
PRICE_PER_MTOK = {"fable": (15.0, 75.0), "opus": (5.0, 25.0), "sonnet": (3.0, 15.0), "haiku": (1.0, 5.0)}
DEFAULT_PRICE = PRICE_PER_MTOK["sonnet"]



def now_utc():
    return datetime.now(timezone.utc)


def now_local():
    return datetime.now()


def parse_utc(value):
    """An aware UTC datetime from a stored UTC string (`Z`, `+00:00` or naive); None when it is not one."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


PROMPT = """Let's do {task_id} from BACKLOG.md. Its summary-table row, entry and any linked spike \
were written for an agent like you to carry out: read them, follow the project's agent \
instructions (AGENTS.md, CLAUDE.md or similar) and do the work.

You are running unattended in a backlog batch, so nobody can answer questions until it ends. \
Use your judgment instead of stopping to ask: where the entry leaves a choice open, pick the \
sensible default that fits the codebase, note it in your commit message and keep going. An item \
that looks large or unfamiliar is not a reason to stop.

Flip the row to 🚧, work through the item as far as you can (test-first where it fits) and run \
the project's full test suite. Set aside only the pieces you truly cannot do from here: a decision \
only a person can make and no reasonable default covers, a write to a live account or production \
system, credentials, or hardware or another machine. Do everything around them.

* All done and green: move it to COMPLETED.md per the backlog conventions, run \
`python tools/check_backlog.py` and commit with {task_id} in the subject.
* Pieces left over: commit the finished, green work with {task_id} in the subject. Keep the entry \
in BACKLOG.md, note what is done, flip its row and Status line to ⏸️ Gated naming what it waits \
on, and add **Open questions** listing each remaining piece, why you couldn't do it and what you'd \
do by default. Run `python tools/check_backlog.py` and commit.
* Tests still red after a real attempt: commit nothing that fails. Leave your edits in the tree, \
flip the row to ⏸️ with **Open questions** saying what fails, what you tried and which files you \
left uncommitted, and commit BACKLOG.md.

Write scripts and multi-line files with your file-writing tool, never a shell heredoc: heredocs truncate in transit (emoji, backslashes) and fail with `unexpected EOF`. If a test fails only in files you did not touch, mention it in one line and move on; do not investigate it.

Never switch branches or revert changes you didn't make. End your reply with exactly one line:
{prefix} done|partial|blocked|failed — <one-line summary>
(partial: some of it committed, the rest left as open questions; blocked: none of it could be done)
"""


def planned_queue(text, attempted=(), include_human=False):
    """The 📋 rows in table order, minus those already attempted this batch."""
    rows = parse_backlog(text)[0]
    return [
        row
        for row in rows
        if row["status"] == "📋"
        and row["id"] not in attempted
        and (include_human or "human" not in row["model_fit"].lower())
    ]


def depends_on(text, task_id):
    """The task IDs named by the entry's `Depends on:` field (it may wrap onto indented lines)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"### {task_id}.")), None)
    if start is None:
        return []
    found, inside = [], False
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if "**Depends on:**" in line:
            inside = True
            line = line.split("**Depends on:**", 1)[1]
        elif not (inside and line.startswith(" ") and line.strip()):
            if inside:
                break
            continue
        found += TASK_ID.findall(line)
    return [d for d in dict.fromkeys(found) if d != task_id]


def target_files(text, row):
    """Files named in the row's Target Surface cell; directories and prose are too coarse to compare."""
    cells = text.splitlines()[row["line"] - 1].split("|")
    return TARGET_FILE.findall(cells[-2] if len(cells) > 2 else "")


def dirty_paths():
    """Uncommitted paths in the tree right now (modified, staged or untracked), repo-relative."""
    paths = []
    for line in git("status", "--porcelain", "--untracked-files=all").splitlines():
        paths.append(line[3:].split(" -> ")[-1].strip('"'))
    return paths


def skip_reason(row, text, rows, dirty, ahead=()):
    """Why this row cannot run now ("" when it can): an unfinished dependency or a dirty target file.

    A dependency counts as done once it has left the table or reads ✅; one in `ahead` is still to run
    earlier in this batch, so it counts too.
    """
    status = {r["id"]: r["status"] for r in rows}
    waiting = [d for d in depends_on(text, row["id"]) if d in status and status[d] != "✅" and d not in ahead]
    if waiting:
        return f"waits on {', '.join(waiting)} (not done, not ahead of it in this run)"
    targets = target_files(text, row)
    clash = sorted({p for p in dirty for t in targets if p.replace(os.sep, "/") == t})
    if clash:
        return f"uncommitted edits in its target files: {', '.join(clash)}"
    return ""


def pickable(queue_rows, text, dirty):
    """`(row, skipped)`: the first row that can run and `{id: reason}` for the ones passed over."""
    rows = parse_backlog(text)[0]
    skipped = {}
    for row in queue_rows:
        reason = skip_reason(row, text, rows, dirty)
        if not reason:
            return row, skipped
        skipped[row["id"]] = reason
    return None, skipped


def newly_pickable(text, skipped_ids):
    """Rows skipped earlier for a dependency that is now done: candidates for the next run."""
    rows = parse_backlog(text)[0]
    status = {r["id"]: r["status"] for r in rows}
    return [
        i
        for i in skipped_ids
        if status.get(i) == "📋" and all(d not in status or status[d] == "✅" for d in depends_on(text, i))
    ]


def claude_alias(name):
    """The Claude Code alias for a guide model name (`Claude Sonnet 5` → `sonnet`); None for others.

    The family alias always resolves to that family's latest model, so a guide that still names an
    older version routes to the current one.
    """
    words = name.lower().split()
    if len(words) > 1 and words[0] == "claude" and words[1] in MODEL_FAMILIES:
        return words[1]
    return None


def agy_resolver(model_ids):
    """A guide-name → agy model id function over the ids `agy models` lists.

    `Gemini 3.8 Flash` → `gemini-3.8-flash-medium`; `Claude Sonnet 5` → `claude-sonnet-5-5-medium`
    (a prefix match, dots and hyphens alike, preferring the `AGY_EFFORT` variant). None when agy
    offers nothing that matches.
    """

    def norm(s):
        return re.sub(r"[\s._]+", "-", s.strip().lower())

    def resolve(name):
        slug = norm(name)
        if not slug:
            return None
        hits = [m for m in model_ids if norm(m) == slug or norm(m).startswith(slug + "-")]
        preferred = [m for m in hits if m.endswith("-" + AGY_EFFORT)]
        return (preferred or hits or [None])[0]

    return resolve


def agy_model_ids(cli):
    """The model ids `agy models` lists (first column), or [] when it can't be run."""
    try:
        proc = subprocess.run(
            [cli, "models"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("agy models failed: %s", e)
        return []
    ids = []
    for line in proc.stdout.splitlines():
        first = line.split("\t")[0].strip()
        if "\t" in line and first and " " not in first:
            ids.append(first)
    return ids


def tier_models(text, overrides=(), resolve=claude_alias):
    """Tier → CLI model: the first model each tier lists in the routing guide's brackets that `resolve` maps.

    `overrides` (`TIER=ALIAS` strings, from `--tier-model`) win over the guide.
    """
    models = {}
    for line in text.splitlines():
        match = GUIDE_TIER.match(line)
        if not match:
            continue
        for name in match.group(2).split(","):
            alias = resolve(name)
            if alias:
                models[match.group(1).lower()] = alias
                break
    for item in overrides:
        tier, _, alias = item.partition("=")
        if tier.strip() and alias.strip():
            models[tier.strip().lower()] = alias.strip()
    return models


def model_for(fit, models):
    """The alias for a Model Fit cell (`Human + Workhorse` routes by its agent tier); "" for none."""
    for part in fit.lower().split("+"):
        alias = models.get(part.strip())
        if alias:
            return alias
    return ""


def build_prompt(task_id, instructions=""):
    """The brief for one item, with any batch-wide extra instructions appended."""
    prompt = PROMPT.format(task_id=task_id, prefix=RESULT_PREFIX)
    if instructions:
        prompt += f"\nExtra instructions for this batch (they win over the brief above where they conflict):\n{instructions}\n"
    return prompt


def extra_instructions(texts, files):
    """`--instructions` texts and `--instructions-file` contents, in that order, one block."""
    parts = [t.strip() for t in texts]
    for path in files:
        with open(path, encoding="utf-8") as f:
            parts.append(f.read().strip())
    return "\n\n".join(p for p in parts if p)


def parse_result(reply):
    """`(result, summary)` from the reply's last BATCH_RESULT line; `("unknown", "")` without one."""
    for line in reversed((reply or "").splitlines()):
        line = line.strip().strip("`*").strip()
        if not line.startswith(RESULT_PREFIX):
            continue
        rest = line[len(RESULT_PREFIX) :].strip()
        word, _, summary = rest.partition(" ")
        word = word.strip("—-:|").lower()
        if word in RESULTS:
            return word, summary.strip(" —-|")
        return "unknown", rest
    return "unknown", ""


def git(*args):
    proc = subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", check=False
    )
    if proc.returncode != 0:
        log.warning("git %s failed: %s", " ".join(args), proc.stderr.strip())
        return ""
    return proc.stdout.strip()


def commits_since(base, task_id):
    if not base:
        return []
    out = git("log", "--format=%h %s", "--fixed-strings", f"--grep={task_id}", f"{base}..HEAD")
    return [line for line in out.splitlines() if line]


def claude_command(args, model, session_id):
    """`claude -p`; the prompt goes in on stdin."""
    cmd = [args.cli, "-p", "--output-format", "json", "--permission-mode", args.permission_mode]
    cmd += ["--session-id", session_id]
    if model:
        cmd += ["--model", model]
    if args.max_budget_usd:
        cmd += ["--max-budget-usd", str(args.max_budget_usd)]
    return cmd


def agy_command(args, model, prompt):
    """`agy --print=<prompt>`: agy reads no prompt from stdin, and `=` keeps a prompt from being read as a flag."""
    cmd = [args.cli, "--output-format", "json"]
    if model:
        cmd += ["--model", model]
    if args.permission_mode == "skip":
        cmd.append("--dangerously-skip-permissions")
    return cmd + [f"--print={prompt}"]


def claude_outcome(payload, returncode):
    """`(reply, error, session, cost, tokens)` from `claude -p --output-format json`; reply None on failure."""
    if not isinstance(payload, dict):
        return None, "", None, None, None
    session, cost = payload.get("session_id"), payload.get("total_cost_usd")
    if returncode == 0 and not payload.get("is_error"):
        return payload.get("result", ""), "", session, cost, None
    return None, (payload.get("result") if payload.get("is_error") else "") or "", session, cost, None


def agy_outcome(payload, returncode):
    """`(reply, error, session, cost, tokens)` from `agy --output-format json`; reply None on failure.

    agy reports `SUCCESS` even when a permission denial ended the turn with nothing done, so any
    `denied_actions` or an empty response counts as failure.
    """
    if not isinstance(payload, dict):
        return None, "", None, None, None
    session = payload.get("conversation_id")
    tokens = (payload.get("usage") or {}).get("total_tokens")
    denied = payload.get("denied_actions") or []
    if denied:
        names = ", ".join(sorted({d.get("display_name") or d.get("action") or "?" for d in denied}))
        error = (
            f"agy denied {names} (it can't ask headless): allow command(*) under permissions.allow in "
            f"{AGY_SETTINGS}, or pass --permission-mode skip"
        )
        return None, error, session, None, tokens
    reply = payload.get("response") or ""
    if returncode == 0 and payload.get("status") == "SUCCESS" and reply.strip():
        return reply, "", session, None, tokens
    return None, f"agy status {payload.get('status')}" + ("" if reply.strip() else ", empty response"), session, None, tokens


OUTCOMES = {"claude": claude_outcome, "agy": agy_outcome}


def held_result(transcript, now, idle_min):
    """The reply holding a BATCH_RESULT line when it is the transcript's last message and has sat quiet.

    A finished session can linger (a background Monitor keeps `claude -p` alive), so the transcript,
    not the process, says the work is over. Any message after the result line clears the hold.
    """
    if not transcript:
        return None
    held, last_at = None, None
    with open(transcript, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:  # the line still being written
                continue
            content = (row.get("message") or {}).get("content")
            if row.get("type") not in ("assistant", "user") or not isinstance(content, list):
                continue
            last_at = parse_utc(row.get("timestamp")) or last_at
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            held = next((t for t in texts if RESULT_PREFIX in t), None) if row["type"] == "assistant" else None
    if held is None or last_at is None or (now - last_at).total_seconds() < idle_min * 60:
        return None
    return held


def end_process(proc):
    """End a session and whatever it spawned (on Windows a plain terminate leaves the children)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run_item(task_id, model, session_id, args, run_dir):
    """Run one headless session; returns the record for the run log."""
    base = git("rev-parse", "HEAD")
    started = now_utc()
    record = {
        "id": task_id,
        "model": model or "default",
        "result": "error",
        "summary": "",
        "commits": [],
        "cost": None,
        "session": session_id,
    }
    out_path = os.path.join(run_dir, f"{task_id}.json")
    err_path = os.path.join(run_dir, f"{task_id}.stderr.txt")
    deadline = time.monotonic() + args.timeout_min * 60
    idle_reply = None  # the held BATCH_RESULT reply once the session sat quiet and was ended
    timed_out = False
    prompt = build_prompt(task_id, args.extra)
    if args.agent == "agy":
        cmd, stdin_text = agy_command(args, model, prompt), None
    else:
        cmd, stdin_text = claude_command(args, model, session_id), prompt
    with open(out_path, "w", encoding="utf-8") as out, open(err_path, "w", encoding="utf-8") as err:
        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            text=True,
            encoding="utf-8",
        )
        if stdin_text is not None:
            proc.stdin.write(stdin_text)
            proc.stdin.close()
        while True:
            try:
                proc.wait(timeout=args.poll_sec)
                break
            except subprocess.TimeoutExpired:
                pass
            transcript = find_transcript(session_id) if args.agent == "claude" else None
            held = held_result(transcript, now_utc(), args.idle_min)
            if held:
                idle_reply = held
                end_process(proc)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                end_process(proc)
                break

    with open(out_path, encoding="utf-8", errors="replace") as f:
        stdout = f.read()
    with open(err_path, encoding="utf-8", errors="replace") as f:
        stderr = f.read()
    if not stderr.strip():
        os.remove(err_path)  # empty; nothing worth keeping

    record["commits"] = commits_since(base, task_id)
    record["minutes"] = round((now_utc() - started).total_seconds() / 60)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = None
    reply, detail, session, cost, tokens = OUTCOMES[args.agent](payload, proc.returncode)
    record["cost"] = cost
    record["session"] = session or session_id
    if tokens is not None:
        record["tokens"] = tokens

    if idle_reply is not None:
        record["result"], record["summary"] = parse_result(idle_reply)
        record["summary"] = f"session ended {args.idle_min} min after its result line; {record['summary']}".strip(" ;")
    elif timed_out:
        record.update(result="timeout", summary=f"no reply after {args.timeout_min} min")
    elif reply is not None:
        record["result"], record["summary"] = parse_result(reply)
    else:
        record["summary"] = (detail or f"exit {proc.returncode} without a result line").splitlines()[0][:300]
        if stderr.strip():
            record["summary"] += f" (stderr in {os.path.basename(err_path)})"

    committed = bool(record["commits"])
    if committed and record["result"] in ("error", "timeout", "unknown"):
        # The work landed even though the reply did not: the commit with the task ID is the evidence.
        was = record["result"]
        record["result"] = "done"
        record["summary"] = f"result recovered from git after {was} ({record['summary']}); {record['commits'][0]}"
    record["committed"] = committed
    return record


def log_header(path, stamp, args):
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Backlog batch {stamp}\n\n")
        f.write(f"Agent `{args.agent}`, branch `{git('rev-parse', '--abbrev-ref', 'HEAD')}`")
        f.write(f", permission mode `{args.permission_mode}`")
        f.write(f", model `{args.model}` for every item.\n\n" if args.model else ", models by Model Fit.\n\n")
        if args.extra:
            quoted = "".join(f"> {line}".rstrip() + "\n" for line in args.extra.splitlines())
            f.write(f"Extra instructions:\n\n{quoted}\n")
        f.write("| ID | Model | Result | Min | Cost | Commits | Summary | Session |\n")
        f.write("|---|---|---|---:|---:|---|---|---|\n")


def commit_note(record):
    """For an item that did not end `done`/`blocked`/`failed` cleanly: whether its work was committed."""
    if record["result"] not in ("error", "timeout", "unknown"):
        return ""
    return " [work committed]" if record.get("commits") else " [nothing committed]"


def spend(record, empty=""):
    """The record's cost in USD, or its token count when the CLI reports no cost (agy)."""
    if isinstance(record.get("cost"), (int, float)):
        return f"${record['cost']:.2f}"
    if isinstance(record.get("tokens"), (int, float)):
        return f"{record['tokens'] / 1000:.0f}k tok"
    return empty


def log_row(path, record):
    cost = spend(record)
    commits = "<br>".join(f"`{c}`" for c in record["commits"])
    summary = (record["summary"] + commit_note(record)).replace("|", "\\|")
    with open(path, "a", encoding="utf-8") as f:
        f.write(
            f"| {record['id']} | {record.get('model', '')} | {record['result']} | {record.get('minutes', '')} "
            f"| {cost} | {commits} | {summary} | `{record['session']}` |\n"
        )


def log_skips(log_path, skipped, passed_over):
    """Print and log each skipped row once per reason; `skipped` accumulates the latest reasons."""
    for skipped_id, reason in passed_over.items():
        if skipped.get(skipped_id) != reason:
            print(f"         skipping {skipped_id}: {reason}", flush=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"| {skipped_id} | | skipped | | | | {reason} | |\n")
        skipped[skipped_id] = reason


def report_ready(backlog, log_path, skipped_ids):
    """Name the skipped rows whose dependency has since finished, so the next run can include them."""
    with open(backlog, encoding="utf-8") as f:
        ready = newly_pickable(f.read(), skipped_ids)
    if not ready:
        return
    note = f"Now pickable (their dependency finished): {', '.join(ready)}. Include them in the next run."
    print(f"\n{note}")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"\n{note}\n")


def write_state(run_dir, state):
    """Replace the batch's state.json in one step, so `--status` never reads half a file."""
    path = os.path.join(run_dir, STATE_FILE)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(path + ".tmp", path)


# --- the monitoring view ------------------------------------------------------------------------


def find_transcript(session_id):
    """The live transcript Claude Code writes for a session, or None before its first turn."""
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    hits = glob.glob(os.path.join(root, "projects", "*", f"{session_id}.jsonl"))
    return hits[0] if hits else None


def one_line(text, width):
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def describe_call(name, args):
    """A tool call as `Name target`: the command, path or pattern that tells calls apart."""
    args = args if isinstance(args, dict) else {}
    target = args.get("command") or args.get("file_path") or args.get("pattern") or args.get("description")
    if target is None:
        target = next((v for v in args.values() if isinstance(v, str)), "")
    target = str(target).replace(PROJECT_ROOT + os.sep, "").replace(PROJECT_ROOT.replace(os.sep, "/") + "/", "")
    return one_line(f"{name} {target}".strip(), 90)


def result_text(block):
    content = block.get("content")
    if isinstance(content, list):
        content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


def usage_cost(model, usage):
    """Estimated USD for one message's token usage, at list price for the model's family."""
    family = next((f for f in MODEL_FAMILIES if f in (model or "")), None)
    price_in, price_out = PRICE_PER_MTOK.get(family, DEFAULT_PRICE)
    created = usage.get("cache_creation") or {}
    hour = created.get("ephemeral_1h_input_tokens", 0)
    write = usage.get("cache_creation_input_tokens", 0) - hour
    tokens = (
        usage.get("input_tokens", 0) * 1.0
        + usage.get("cache_read_input_tokens", 0) * 0.1
        + max(write, 0) * 1.25
        + hour * 2.0
    )
    return (tokens * price_in + usage.get("output_tokens", 0) * price_out) / 1e6


def transcript_digest(path, now):
    """What a running session has done so far, from its transcript: counts, the tail, the last words."""
    turns, calls, results = 0, [], []
    last_at, last_text = None, ""
    costs = {}  # message id → cost; a streamed message can be written more than once
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:  # the line still being written
                continue
            content = (row.get("message") or {}).get("content")
            if row.get("type") not in ("assistant", "user") or not isinstance(content, list):
                continue
            last_at = parse_utc(row.get("timestamp")) or last_at
            if row["type"] == "assistant":
                turns += 1
                message = row["message"]
                if isinstance(message.get("usage"), dict):
                    costs[message.get("id") or f"row{turns}"] = usage_cost(message.get("model"), message["usage"])
            for block in content:
                kind = block.get("type") if isinstance(block, dict) else None
                if kind == "tool_use":
                    calls.append(describe_call(block.get("name", "?"), block.get("input")))
                elif kind == "text" and block.get("text", "").strip():
                    last_text = block["text"]
                elif kind == "tool_result":
                    results.append((bool(block.get("is_error")), result_text(block)))

    errors = [text for is_error, text in results if is_error]
    recent_errors = [text for is_error, text in results[-RECENT_CALLS:] if is_error]
    repeated = [(call, n) for call, n in Counter(calls[-RECENT_CALLS:]).most_common(2) if n >= 3]
    return {
        "turns": turns,
        "cost": sum(costs.values()),
        "calls": len(calls),
        "idle_min": round((now - last_at).total_seconds() / 60) if last_at else None,
        "last_calls": calls[-6:],
        "recent_errors": len(recent_errors),
        "last_error": one_line(recent_errors[-1], 160) if recent_errors else "",
        "denials": sum(1 for text in errors if DENIAL_RE.search(text)),
        "repeated": repeated,
        "last_text": one_line(last_text, 200),
    }


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":  # os.kill(pid, 0) would terminate the process on Windows
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def files_touched_since(started):
    """Uncommitted paths modified after the batch started: an item's leftovers, or a teammate's edits."""
    cutoff = started.timestamp()
    paths = []
    for line in git("status", "--porcelain", "--untracked-files=all").splitlines():
        path = line[3:].split(" -> ")[-1].strip('"')
        full = os.path.join(PROJECT_ROOT, path)
        try:
            if os.path.getmtime(full) >= cutoff:
                paths.append(path)
        except OSError:  # deleted in the tree
            paths.append(path)
    return paths


def latest_run_dir(stamp=None):
    if stamp:
        return os.path.join(LOG_ROOT, stamp)
    dirs = sorted(d for d in glob.glob(os.path.join(LOG_ROOT, "*")) if os.path.isfile(os.path.join(d, STATE_FILE)))
    return dirs[-1] if dirs else None


def batch_marker(run_dir):
    """What `--status --wait` watches: the running item's id, or the end of the batch."""
    try:
        with open(os.path.join(run_dir, STATE_FILE), encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):  # no batch yet, or state.json caught mid-replace
        return None
    return (state.get("current") or {}).get("id"), bool(state.get("ended")), len(state.get("finished") or [])


def wait_for_change(run_dir, poll_sec, sleep=time.sleep):
    """Block until the running item changes or the batch ends; return at once if there is no live batch."""
    first = batch_marker(run_dir)
    if first is None or first[1]:
        return
    while batch_marker(run_dir) == first:
        sleep(poll_sec)


def render_status(run_dir, backlog_path=DEFAULT_BACKLOG):
    """The monitoring view of one batch as text."""
    state_path = os.path.join(run_dir or "", STATE_FILE)
    if not run_dir or not os.path.isfile(state_path):
        return f"No batch state under {LOG_ROOT} (batches started before --status existed have none)."
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    now = now_utc()
    started = parse_utc(state["started"])
    finished = state["finished"]
    current = state.get("current")

    if state.get("ended"):
        status = f"ended ({state.get('stop_reason') or 'queue empty'})"
    elif pid_alive(state.get("pid")):
        status = f"running (pid {state['pid']})"
    else:
        status = "DIED: its process is gone and it recorded no end"
    tally = Counter(r["result"] for r in finished)
    spent = sum(r["cost"] for r in finished if isinstance(r.get("cost"), (int, float)))
    tokens = sum(r["tokens"] for r in finished if isinstance(r.get("tokens"), (int, float)))
    so_far = " + ".join(
        part for part in (f"${spent:.2f}" if spent or not tokens else "", f"{tokens / 1000:.0f}k tok" if tokens else "") if part
    )
    lines = [
        f"Batch {state['stamp']} · {state.get('agent', 'claude')} · {status} · "
        f"{round((now - started).total_seconds() / 60)} min in",
        f"Results: {', '.join(f'{n} {k}' for k, n in tally.items()) or 'none yet'} · {so_far} so far",
    ]

    problems = check(os.path.dirname(os.path.abspath(backlog_path)), MAX_LINES)[0]
    lines.append("Backlog check: OK" if not problems else f"Backlog check: FAILS ({len(problems)}): {problems[0]}")
    commits = [c for c in git("log", "--format=%h %s", f"{state['base']}..HEAD").splitlines() if c]
    touched = files_touched_since(started)
    lines.append(f"Tree: {len(commits)} commit(s) since the start · {len(touched)} uncommitted path(s) touched since")
    lines += [f"  {path}" for path in touched[:8]]
    if len(touched) > 8:
        lines.append(f"  … {len(touched) - 8} more")

    if finished:
        lines.append("Finished:")
        for r in finished:
            cost = spend(r, "-")
            lines.append(
                f"  {r['id']} {r['model']} {r['result']} {r.get('minutes', '?')}m {cost} "
                f"{len(r['commits'])}c — {one_line(r['summary'], 110)}{commit_note(r)}"
            )

    if current and not state.get("ended"):
        minutes = round((now - parse_utc(current["started"])).total_seconds() / 60)
        agent = state.get("agent", "claude")
        session = current["session"] or "known when it ends"
        lines.append(f"Current: {current['id']} ({current['model']}) {minutes} min, {agent} session {session}")
        transcript = find_transcript(current["session"]) if agent == "claude" else None
        if agent != "claude":
            lines.append(f"  {agent} conversations can't be read live: judge it by the tree and commits above")
        elif not transcript:
            lines.append("  no transcript yet")
        else:
            d = transcript_digest(transcript, now)
            idle = "?" if d["idle_min"] is None else f"{d['idle_min']} min"
            lines.append(
                f"  {d['turns']} turns, {d['calls']} tool calls, ≈${d['cost']:.2f} so far (estimate), "
                f"last activity {idle} ago"
            )
            lines += [f"  > {call}" for call in d["last_calls"]]
            lines.append(f"  Errors in the last {RECENT_CALLS} results: {d['recent_errors']}")
            if d["last_error"]:
                lines.append(f"    latest: {d['last_error']}")
            lines.append(f"  Permission denials so far: {d['denials']}")
            for call, n in d["repeated"]:
                lines.append(f"  Repeated ×{n} in the last {RECENT_CALLS}: {call}")
            if d["last_text"]:
                lines.append(f"  Last said: {d['last_text']}")

    if not state.get("ended"):
        with open(backlog_path, encoding="utf-8") as f:
            text = f.read()
        attempted = [r["id"] for r in finished] + ([current["id"]] if current else [])
        only = set(state.get("only") or [])
        queue = [r for r in planned_queue(text, attempted, state.get("include_human")) if not only or r["id"].upper() in only]
        resolve = agy_resolver(state.get("agy_models") or []) if state.get("agent") == "agy" else claude_alias
        models = tier_models(text, state.get("tier_models") or (), resolve)
        left = [f"{r['id']} {state.get('model') or model_for(r['model_fit'], models) or 'default'}" for r in queue]
        lines.append(f"Queue: {len(left)} left" + (f": {', '.join(left)}" if left else ""))
    return "\n".join(lines)


# --- the batch ----------------------------------------------------------------------------------


def default_agent(env=None, which=shutil.which):
    """`$BACKLOG_AGENT` when it names a known agent, else `claude` when it's on PATH, else `agy` when that is."""
    env = os.environ if env is None else env
    named = (env.get("BACKLOG_AGENT") or "").strip().lower()
    if named in AGENTS:
        return named
    return next((a for a in AGENTS if which(a)), "claude")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--agent",
        choices=AGENTS,
        default=default_agent(),
        help="the CLI that works each item (default: $BACKLOG_AGENT, else claude if on PATH, else agy)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the queue and the prompt, run nothing")
    parser.add_argument(
        "--status",
        nargs="?",
        const="",
        default=None,
        metavar="STAMP",
        help="the monitoring view of a batch (default: the latest)",
    )
    parser.add_argument(
        "--wait", action="store_true", help="with --status: block until the running item changes or the batch ends"
    )
    parser.add_argument("--limit", type=int, default=0, help="stop after N items (default: all)")
    parser.add_argument("--only", default="", help="comma-separated IDs; still taken in table order")
    parser.add_argument("--include-human", action="store_true", help="also take rows whose Model Fit names a human")
    parser.add_argument(
        "--model", default="", help="one model for every item (default: each row's Model Fit, per the routing guide)"
    )
    parser.add_argument(
        "--tier-model",
        action="append",
        default=[],
        metavar="TIER=ALIAS",
        help="route a Model Fit tier to a model alias, e.g. workhorse=sonnet (repeatable; wins over the routing guide)",
    )
    parser.add_argument(
        "--permission-mode",
        default=None,
        help="claude: its --permission-mode (default: auto). agy: `settings` (default; the grants in its "
        "settings.json) or `skip` (--dangerously-skip-permissions)",
    )
    parser.add_argument(
        "--instructions",
        action="append",
        default=[],
        metavar="TEXT",
        help="extra instructions appended to every item's prompt (repeatable)",
    )
    parser.add_argument(
        "--instructions-file",
        action="append",
        default=[],
        metavar="PATH",
        help="a file of extra instructions appended to every item's prompt (repeatable; for multi-line text)",
    )
    parser.add_argument("--max-budget-usd", type=float, default=0, help="claude only: per-item spend cap (default: none)")
    parser.add_argument("--timeout-min", type=int, default=120, help="per-item wall clock cap (default: 120)")
    parser.add_argument(
        "--idle-min",
        type=float,
        default=3,
        help="end a session this many minutes after its BATCH_RESULT line if it stays quiet (default: 3)",
    )
    parser.add_argument("--poll-sec", type=float, default=15, help="how often a running session is checked")
    parser.add_argument("--backlog", default=DEFAULT_BACKLOG, help="the backlog file (default: BACKLOG.md at the repo root)")
    parser.add_argument(
        "--cli", "--claude", dest="cli", default=None, help="the agent's executable (default: `claude` or `agy` on PATH)"
    )
    args = parser.parse_args(argv)
    try:
        args.extra = extra_instructions(args.instructions, args.instructions_file)
    except OSError as e:
        parser.error(f"--instructions-file: {e}")
    args.cli = args.cli or shutil.which(args.agent) or args.agent
    if args.agent == "agy":
        args.permission_mode = args.permission_mode or "settings"
        if args.permission_mode not in ("settings", "skip"):
            parser.error("--permission-mode for agy is `settings` or `skip`")
        if args.max_budget_usd:
            parser.error("--max-budget-usd is claude-only: agy has no spend cap")
    else:
        args.permission_mode = args.permission_mode or "auto"

    if args.status is not None:
        run_dir = latest_run_dir(args.status or None)
        if args.wait and run_dir:
            wait_for_change(run_dir, args.poll_sec)
        print(render_status(run_dir, args.backlog))
        return 0

    only = {i.strip().upper() for i in args.only.split(",") if i.strip()}
    agy_models = agy_model_ids(args.cli) if args.agent == "agy" and not args.model else []
    resolve = agy_resolver(agy_models) if args.agent == "agy" else claude_alias

    def queue(attempted):
        """The rows still to run, the tier → model map and the backlog text, all as the backlog is now."""
        with open(args.backlog, encoding="utf-8") as f:
            text = f.read()
        rows = planned_queue(text, attempted, args.include_human)
        models = tier_models(text, args.tier_model, resolve)
        return [r for r in rows if not only or r["id"].upper() in only], models, text

    def pick_model(row, models):
        return args.model or model_for(row["model_fit"], models)

    if args.dry_run:
        rows, models, text = queue(())
        print(f"Agent: {args.agent} ({args.cli}), permission mode {args.permission_mode}")
        print(f"{len(rows)} planned item(s), in order:")
        table, dirty, ahead = parse_backlog(text)[0], dirty_paths(), []
        for row in rows:
            reason = skip_reason(row, text, table, dirty, ahead)
            if not reason:
                ahead.append(row["id"])
            if len(ahead) > (args.limit or len(rows)):
                break
            note = f"SKIPPED: {reason}" if reason else f"→ {pick_model(row, models) or 'default'}"
            print(f"  {row['id']:<9} {row['model_fit']:<20} {note}")
        print("\nPrompt for each item:\n")
        print(build_prompt("<ID>", args.extra))
        return 0

    stamp = now_local().strftime("%Y%m%d-%H%M")
    run_dir = os.path.join(LOG_ROOT, stamp)
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(LOG_ROOT, f"{stamp}.md")
    log_header(log_path, stamp, args)
    state = {
        "stamp": stamp,
        "pid": os.getpid(),
        "agent": args.agent,
        "agy_models": agy_models,
        "started": now_utc().isoformat(),
        "base": git("rev-parse", "HEAD"),
        "log": log_path,
        "model": args.model,
        "tier_models": args.tier_model,
        "only": sorted(only),
        "include_human": args.include_human,
        "instructions": args.extra,
        "current": None,
        "finished": [],
        "ended": None,
        "stop_reason": "",
    }
    write_state(run_dir, state)

    def finish(reason=""):
        state.update(current=None, ended=now_utc().isoformat(), stop_reason=reason)
        write_state(run_dir, state)

    attempted, skipped = [], {}
    while not args.limit or len(attempted) < args.limit:
        rows, models, text = queue(attempted)
        row, passed_over = pickable(rows, text, dirty_paths())
        log_skips(log_path, skipped, passed_over)
        state["skipped"] = skipped
        if row is None:
            break
        task_id = row["id"]
        model = pick_model(row, models)
        session_id = str(uuid.uuid4()) if args.agent == "claude" else ""  # agy names its conversation at the end
        attempted.append(task_id)
        state["current"] = {
            "id": task_id,
            "model": model or "default",
            "session": session_id,
            "started": now_utc().isoformat(),
        }
        write_state(run_dir, state)
        print(f"[{now_local():%H:%M}] {task_id} ({model or 'default model'}) …", flush=True)
        try:
            record = run_item(task_id, model, session_id, args, run_dir)
        except KeyboardInterrupt:
            log_row(
                log_path, {"id": task_id, "result": "interrupted", "summary": "", "commits": [], "session": session_id}
            )
            finish(f"interrupted during {task_id}")
            print("interrupted; stopping the batch")
            break
        log_row(log_path, record)
        state["finished"].append(record)
        state["current"] = None
        write_state(run_dir, state)
        print(f"         {record['result']}: {record['summary']}", flush=True)
        if record["result"] in ("error", "timeout"):
            finish(f"{record['result']} on {task_id}")
            print("the CLI errored or timed out; stopping the batch so nothing else starts on a bad tree")
            break
    if not state["ended"]:
        finish("limit reached" if args.limit and len(attempted) >= args.limit else "")

    report_ready(args.backlog, log_path, [i for i in skipped if i not in attempted])
    print(f"\n{len(attempted)} item(s) attempted. Run log: {log_path}")
    rel = os.path.relpath(log_path, PROJECT_ROOT).replace(os.sep, "/")
    print("Morning review, from the repo root:")
    ask = "agy -i" if args.agent == "agy" else "claude"
    print(f'  {ask} "Review the backlog batch log {rel}: walk me through each blocked or failed item."')
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
