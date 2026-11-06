"""The unattended backlog runner: queue order, result parsing and the session loop (no real CLI)."""

import json
import os
import subprocess
import sys
from datetime import timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import batch_backlog  # noqa: E402

BACKLOG = """# Backlog

## Model Selection & Routing Guide

* **Planner** (Claude Fable 5.1, Claude Opus 5.5): plans.
* **Frontier** (Claude Opus 5.5, Gemini 4 Argon): hard things.
* **Workhorse** (Claude Sonnet 5): most things.
* **Fast** (Gemini 3.8 Flash): mechanical things.
* **Human** (the athlete): hands.

## Active Engineering Backlog

| ID | Task Name | Focus Area | Status | Model Fit | Target Surface / Script |
|:---|:---|:---|:---|:---|:---|
| **B1.4242** | [Busy One](#b14242-busy-one) | Area | 🚧 In Progress | **Workhorse** | `a.py` |
| **B2.101** | [First Planned](#b2101-first-planned) | Area | 📋 Planned | **Workhorse** | `b.py` |
| **B2.99** | [Needs Hands](#b299-needs-hands) | Area | 📋 Planned | **Human + Workhorse** | a laptop |
| **B3.7** | [Gated](#b37-gated) | Area | ⏸️ Gated (Ask First) | **Frontier** | `c.py` |
| **B3.8** | [Last Planned](#b38-last-planned) | Area | 📋 Planned | **Frontier** | `d.py` |
"""


def ids(rows):
    return [r["id"] for r in rows]


def test_queue_is_planned_rows_in_table_order_without_human_rows():
    assert ids(batch_backlog.planned_queue(BACKLOG)) == ["B2.101", "B3.8"]


def test_queue_takes_human_rows_on_request_and_skips_attempted():
    rows = batch_backlog.planned_queue(BACKLOG, attempted=["B2.101"], include_human=True)
    assert ids(rows) == ["B2.99", "B3.8"]


def test_tier_models_take_the_first_claude_family_in_each_tier():
    assert batch_backlog.tier_models(BACKLOG) == {"planner": "fable", "frontier": "opus", "workhorse": "sonnet"}


@pytest.mark.parametrize(
    "fit, expected",
    [("Workhorse", "sonnet"), ("Frontier", "opus"), ("Human + Workhorse", "sonnet"), ("Fast", ""), ("Human", "")],
    ids=["workhorse", "frontier", "human-plus-tier", "no-claude-model", "human-only"],
)
def test_model_for(fit, expected):
    assert batch_backlog.model_for(fit, batch_backlog.tier_models(BACKLOG)) == expected


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("work\nBATCH_RESULT: done — added the thing", ("done", "added the thing")),
        ("BATCH_RESULT: blocked - needs a hub decision\n", ("blocked", "needs a hub decision")),
        ("`BATCH_RESULT: failed — tests red`", ("failed", "tests red")),
        ("BATCH_RESULT: partial — 3 of 4 done", ("partial", "3 of 4 done")),
        ("BATCH_RESULT: done|blocked|failed — x\nBATCH_RESULT: done — real", ("done", "real")),
        ("no marker at all", ("unknown", "")),
        ("BATCH_RESULT: maybe — hmm", ("unknown", "maybe — hmm")),
    ],
    ids=["done", "blocked-hyphen", "backticked", "partial", "last-line-wins", "missing", "bad-word"],
)
def test_parse_result(reply, expected):
    assert batch_backlog.parse_result(reply) == expected


def test_prompt_names_the_item_and_the_result_line():
    prompt = batch_backlog.build_prompt("B2.101")
    assert "B2.101" in prompt and "BATCH_RESULT:" in prompt


class FakeSession:
    """One fake `claude -p`: what it prints, how it exits, and whether it lingers after finishing."""

    def __init__(self, result_text, returncode=0, hang=False, transcript_text=None, stderr="", commits=(), denied=()):
        self.result_text, self.returncode, self.hang = result_text, returncode, hang
        self.transcript_text, self.stderr, self.commits = transcript_text, stderr, list(commits)
        self.denied = list(denied)  # agy only: the display names of actions it denied
        self.cursor_events = []  # cursor only: events streamed before the reply
        self.prompt = ""
        self.ended = False
        self.pid = 1


class FakeStdin:
    def __init__(self, session):
        self.session = session

    def write(self, text):
        self.session.prompt += text

    def close(self):
        pass


class FakePopen:
    """`subprocess.Popen` stand-in that plays out a FakeSession."""

    def __init__(self, sessions, config_dir):
        self.sessions, self.config_dir = iter(sessions), config_dir

    def __call__(self, cmd, **kwargs):
        session = next(self.sessions)
        session.stdin = FakeStdin(session)
        session.wait = lambda timeout=None: self._wait(session)
        if "--trust" in cmd:  # cursor: the prompt comes on stdin, the output is an NDJSON event stream
            return self._cursor(session, kwargs)
        if "--session-id" not in cmd:  # agy: the prompt rides in argv and the payload has its own shape
            assert kwargs["stdin"] is subprocess.DEVNULL and cmd[-1].startswith("--print=")
            session.prompt = cmd[-1][len("--print=") :]
            if session.result_text is not None and not session.hang:
                payload = {
                    "conversation_id": "conv-1",
                    "status": "SUCCESS",
                    "response": "" if session.denied else session.result_text,
                    "usage": {"total_tokens": 42_000},
                }
                if session.denied:
                    payload["denied_actions"] = [{"action": "command", "display_name": d} for d in session.denied]
                kwargs["stdout"].write(json.dumps(payload))
            kwargs["stderr"].write(session.stderr)
            return session
        session_id = cmd[cmd.index("--session-id") + 1]
        if session.result_text is not None:
            payload = {
                "result": session.result_text,
                "session_id": "abc123",
                "total_cost_usd": 1.5,
                "is_error": session.returncode != 0,
            }
            if not session.hang:
                kwargs["stdout"].write(json.dumps(payload))
        kwargs["stderr"].write(session.stderr)
        if session.transcript_text is not None:
            projects = self.config_dir / "projects" / "c--repo"
            projects.mkdir(parents=True, exist_ok=True)
            at = (batch_backlog.now_utc() - timedelta(minutes=10)).isoformat()
            row = transcript_row("assistant", [{"type": "text", "text": session.transcript_text}], at)
            (projects / f"{session_id}.jsonl").write_text(row, encoding="utf-8")
        return session

    @staticmethod
    def _cursor(session, kwargs):
        assert kwargs["stdin"] is subprocess.PIPE
        out = kwargs["stdout"]
        events = [{"type": "system", "subtype": "init", "session_id": "chat-1", "model": "x"}]
        events += session.cursor_events
        if session.result_text is not None:
            text = [{"type": "text", "text": session.result_text}]
            events.append({"type": "assistant", "message": {"role": "assistant", "content": text}})
            if not session.hang and session.returncode == 0:
                usage = {"inputTokens": 20_000, "outputTokens": 5_000, "cacheReadTokens": 30_000, "cacheWriteTokens": 0}
                events.append(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": session.result_text,
                        "session_id": "chat-1",
                        "usage": usage,
                    }
                )
        out.write("".join(json.dumps(e) + "\n" for e in events))
        out.flush()
        if session.hang:  # it went quiet ten minutes ago
            old = batch_backlog.now_utc().timestamp() - 600
            os.utime(out.name, (old, old))
        kwargs["stderr"].write(session.stderr)
        return session

    @staticmethod
    def _wait(session):
        if session.hang and not session.ended:
            raise subprocess.TimeoutExpired("claude", 1)
        return session.returncode


def fake_session(result_text, returncode=0, **kwargs):
    return FakeSession(result_text, returncode, **kwargs)


def run_main(tmp_path, sessions, *extra):
    backlog = tmp_path / "BACKLOG.md"
    backlog.write_text(BACKLOG, encoding="utf-8")
    config_dir = tmp_path / "claude"

    def end(proc):
        proc.ended = True
        proc.returncode = 1

    def commits_since(base, task_id):
        return next((s.commits for s in sessions if s.prompt and task_id in s.prompt), [])

    with (
        patch.dict("os.environ", {"CLAUDE_CONFIG_DIR": str(config_dir)}),
        patch.object(batch_backlog, "LOG_ROOT", str(tmp_path / "logs")),
        patch.object(batch_backlog, "git", return_value=""),
        patch.object(batch_backlog, "end_process", side_effect=end),
        patch.object(batch_backlog, "commits_since", side_effect=commits_since),
        patch("batch_backlog.subprocess.Popen", side_effect=FakePopen(sessions, config_dir)) as run,
    ):
        batch_backlog.main(["--backlog", str(backlog), "--agent", "claude", "--cli", "claude", *extra])
    (log,) = (tmp_path / "logs").glob("*.md")
    return run, log.read_text(encoding="utf-8")


def test_each_item_gets_its_own_session_and_a_log_row(tmp_path):
    sessions = [fake_session("BATCH_RESULT: done — one"), fake_session("BATCH_RESULT: blocked — two")]
    run, log = run_main(tmp_path, sessions)
    assert run.call_count == 2
    assert "B2.101" in sessions[0].prompt
    assert "B3.8" in sessions[1].prompt
    assert "| B2.101 | sonnet | done |" in log and "| B3.8 | opus | blocked |" in log
    assert "$1.50" in log and "`abc123`" in log
    first, second = (call.args[0] for call in run.call_args_list)
    assert first[first.index("--model") + 1] == "sonnet"
    assert second[second.index("--model") + 1] == "opus"


def test_each_session_gets_its_own_id_and_the_state_records_it(tmp_path):
    run, _ = run_main(tmp_path, [fake_session("BATCH_RESULT: done — one"), fake_session("BATCH_RESULT: done — two")])
    sessions = [call.args[0][call.args[0].index("--session-id") + 1] for call in run.call_args_list]
    assert len(set(sessions)) == 2
    (state_path,) = (tmp_path / "logs").glob("*/state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [r["id"] for r in state["finished"]] == ["B2.101", "B3.8"]
    assert state["current"] is None and state["ended"] and state["stop_reason"] == ""


def test_extra_instructions_reach_every_prompt_the_log_and_the_state(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text("Use the staging config.\nSkip the docs site.\n", encoding="utf-8")
    sessions = [fake_session("BATCH_RESULT: done — one"), fake_session("BATCH_RESULT: done — two")]
    _, log = run_main(tmp_path, sessions, "--instructions", "Prefer pathlib.", "--instructions-file", str(notes))
    for session in sessions:
        assert session.prompt.rstrip().endswith("Prefer pathlib.\n\nUse the staging config.\nSkip the docs site.")
    assert "> Prefer pathlib.\n>\n> Use the staging config." in log
    (state_path,) = (tmp_path / "logs").glob("*/state.json")
    assert "Skip the docs site." in json.loads(state_path.read_text(encoding="utf-8"))["instructions"]


def test_no_extra_instructions_leaves_the_brief_alone():
    assert batch_backlog.build_prompt("B2.1", "") == batch_backlog.build_prompt("B2.1")
    assert "Extra instructions" not in batch_backlog.build_prompt("B2.1")


def test_model_flag_overrides_every_item(tmp_path):
    run, log = run_main(tmp_path, [fake_session("BATCH_RESULT: done — x")], "--model", "haiku", "--limit", "1")
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("--model") + 1] == "haiku"
    assert "| B2.101 | haiku | done |" in log


def test_a_cli_error_stops_the_batch(tmp_path):
    run, log = run_main(tmp_path, [fake_session("usage limit reached", returncode=1)])
    assert run.call_count == 1
    assert "| B2.101 | sonnet | error |" in log and "usage limit reached" in log
    assert "[nothing committed]" in log


def test_a_finished_session_that_lingers_is_ended_and_its_result_kept(tmp_path):
    lingering = fake_session(
        None, hang=True, transcript_text="Done.\nBATCH_RESULT: done — added it", commits=["a1 feat: x (B2.101)"]
    )
    run, log = run_main(tmp_path, [lingering, fake_session("BATCH_RESULT: blocked — two")])
    assert lingering.ended and run.call_count == 2  # the batch carried on to the next item
    assert "| B2.101 | sonnet | done |" in log and "added it" in log and "`a1 feat: x (B2.101)`" in log


def test_a_session_still_working_is_left_alone(tmp_path):
    busy = fake_session(None, hang=True, transcript_text="working on it")
    # No result line in its transcript, so the idle check never fires; only the wall clock ends it.
    _, log = run_main(tmp_path, [busy], "--timeout-min", "0", "--limit", "1")
    assert busy.ended
    assert "| B2.101 | sonnet | timeout |" in log and "[nothing committed]" in log


def test_a_session_that_dies_after_committing_is_recorded_as_done(tmp_path):
    warning = "Ignoring 87 permissions.allow entries…"
    dead = fake_session(None, returncode=1, stderr=warning, commits=["b2 feat: y (B2.101)"])
    _, log = run_main(tmp_path, [dead, fake_session("BATCH_RESULT: done — two")])
    assert "| B2.101 | sonnet | done |" in log and "result recovered from git" in log
    assert "Ignoring 87" not in log


def test_a_session_that_fails_outright_never_shows_stderr_as_its_summary(tmp_path):
    dead = fake_session(None, returncode=1, stderr="Ignoring 87 permissions.allow entries…")
    run, log = run_main(tmp_path, [dead])
    assert run.call_count == 1  # an uncommitted failure still stops the batch
    assert "| B2.101 | sonnet | error |" in log and "exit 1 without a result line" in log
    assert "Ignoring 87" not in log and "[nothing committed]" in log


def test_held_result_needs_the_result_to_be_the_last_message_and_quiet(tmp_path):
    now = batch_backlog.now_utc()
    old = (now - timedelta(minutes=10)).isoformat()
    text = [{"type": "text", "text": "BATCH_RESULT: done — x"}]
    path = tmp_path / "s.jsonl"

    path.write_text(transcript_row("assistant", text, old), encoding="utf-8")
    assert batch_backlog.held_result(str(path), now, 3) == "BATCH_RESULT: done — x"
    assert batch_backlog.held_result(str(path), now, 30) is None  # not idle long enough

    follow_up = transcript_row("assistant", [bash("git status")], old)
    path.write_text(transcript_row("assistant", text, old) + "\n" + follow_up, encoding="utf-8")
    assert batch_backlog.held_result(str(path), now, 3) is None  # it kept working after the line
    assert batch_backlog.held_result(None, now, 3) is None


def transcript_row(kind, blocks, at):
    return json.dumps({"type": kind, "timestamp": at, "message": {"content": blocks}})


def bash(command):
    return {"type": "tool_use", "name": "Bash", "input": {"command": command}}


def test_status_reports_the_running_session_from_its_transcript(tmp_path, monkeypatch):
    now = batch_backlog.now_utc()
    at = (now - timedelta(minutes=12)).isoformat()
    session = "0000-4242"
    run_dir = tmp_path / "logs" / "20260101-0100"
    run_dir.mkdir(parents=True)
    batch_backlog.write_state(
        str(run_dir),
        {
            "stamp": "20260101-0100",
            "pid": 4242,
            "started": (now - timedelta(minutes=40)).isoformat(),
            "base": "abc",
            "model": "",
            "only": [],
            "include_human": False,
            "current": {"id": "B3.8", "model": "opus", "session": session, "started": at},
            "finished": [
                {
                    "id": "B2.101",
                    "model": "sonnet",
                    "result": "done",
                    "minutes": 20,
                    "cost": 2.5,
                    "commits": ["a1 feat: x (B2.101)"],
                    "summary": "added x",
                    "session": "s1",
                },
            ],
            "ended": None,
            "stop_reason": "",
        },
    )
    projects = tmp_path / "claude" / "projects" / "c--repo"
    projects.mkdir(parents=True)
    denied = {"type": "tool_result", "is_error": True, "content": "Permission to use Bash has been denied."}
    (projects / f"{session}.jsonl").write_text(
        "\n".join(
            [transcript_row("assistant", [bash("pytest tests/test_x.py -q")], at)] * 3
            + [
                transcript_row("user", [denied], at),
                transcript_row("assistant", [{"type": "text", "text": "Tests still red; trying a fixture."}], at),
                '{"type": "assistant", "message": {"content": [',  # a half-written last line
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    backlog = tmp_path / "BACKLOG.md"
    backlog.write_text(BACKLOG, encoding="utf-8")
    with (
        patch.object(batch_backlog, "LOG_ROOT", str(tmp_path / "logs")),
        patch.object(batch_backlog, "git", return_value=""),
        patch.object(batch_backlog, "pid_alive", return_value=True),
        patch.object(batch_backlog, "check", return_value=([], 0, None)),
    ):
        text = batch_backlog.render_status(batch_backlog.latest_run_dir(), str(backlog))

    assert "running (pid 4242)" in text and "1 done" in text and "$2.50" in text
    assert "Backlog check: OK" in text
    assert "B2.101 sonnet done 20m $2.50 1c — added x" in text
    assert "Current: B3.8 (opus) 12 min" in text
    assert "4 turns, 3 tool calls, ≈$0.00 so far (estimate), last activity 12 min ago" in text
    assert "Repeated ×3 in the last 20: Bash pytest tests/test_x.py -q" in text
    assert "Permission denials so far: 1" in text
    assert "Last said: Tests still red; trying a fixture." in text
    assert "Queue: 0 left" in text
    assert len(text.splitlines()) < 25


def test_status_without_a_batch(tmp_path):
    with patch.object(batch_backlog, "LOG_ROOT", str(tmp_path)):
        assert batch_backlog.render_status(batch_backlog.latest_run_dir()).startswith("No batch state")


def test_limit_and_only(tmp_path):
    session = fake_session("BATCH_RESULT: done — x")
    run, _ = run_main(tmp_path, [session], "--only", "b3.8", "--limit", "1")
    assert run.call_count == 1
    assert "B3.8" in session.prompt


DEPENDENT = """## Active Engineering Backlog

| ID | Task Name | Focus Area | Status | Model Fit | Target Surface / Script |
|:---|:---|:---|:---|:---|:---|
| **B2.201** | [Dependent](#b2201-dependent) | Area | 📋 Planned | **Workhorse** | `a.py` |
| **B2.202** | [Base](#b2202-base) | Area | 📋 Planned | **Workhorse** | `b.py`, `tests/` |
| **B2.203** | [Gated Base](#b2203-gated-base) | Area | ⏸️ Gated | **Workhorse** | `c.py` |
| **B2.204** | [Needs Gated](#b2204-needs-gated) | Area | 📋 Planned | **Workhorse** | `d.py` |
| **B2.205** | [Needs Done](#b2205-needs-done) | Area | 📋 Planned | **Workhorse** | `e.py` |

### B2.201. Dependent
* **Status:** 📋 **Planned**
* **Depends on:** B2.202 (build the base first).

### B2.204. Needs Gated
* **Depends on:** B2.203,
  B2.999 (done, §1).
* **End goal:** x
"""


def pick(text, dirty=()):
    queue = batch_backlog.planned_queue(text)
    row, skipped = batch_backlog.pickable(queue, text, list(dirty))
    return row["id"] if row else None, skipped


def test_depends_on_reads_wrapped_ids_and_ignores_prose():
    assert batch_backlog.depends_on(DEPENDENT, "B2.204") == ["B2.203", "B2.999"]
    assert batch_backlog.depends_on(DEPENDENT, "B2.201") == ["B2.202"]
    assert batch_backlog.depends_on(DEPENDENT, "B2.205") == []


def test_a_row_waits_for_a_dependency_that_is_still_planned():
    picked, skipped = pick(DEPENDENT)
    assert picked == "B2.202"
    assert "waits on B2.202" in skipped["B2.201"]


def test_a_gated_dependency_blocks_but_a_dependency_gone_from_the_table_does_not():
    text = DEPENDENT.replace(
        "| **B2.202** | [Base](#b2202-base) | Area | 📋 Planned", "| **B2.202** | [Base](#b2202-base) | Area | ✅ Done"
    )
    picked, skipped = pick(text)
    assert picked == "B2.201"
    picked, skipped = pick(text.replace("📋 Planned | **Workhorse** | `a.py`", "🚧 | **Workhorse** | `a.py`"))
    assert picked == "B2.205" and "waits on B2.203" in skipped["B2.204"]


def test_a_dirty_target_file_skips_the_row_and_names_the_file():
    text = DEPENDENT.replace("* **Depends on:** B2.202 (build the base first).\n", "")
    picked, skipped = pick(text, dirty=["a.py"])
    assert picked != "B2.201"
    assert "a.py" in skipped["B2.201"]


def test_directory_targets_do_not_count_as_overlap():
    picked, _ = pick(DEPENDENT, dirty=["tests/test_other.py"])
    assert picked == "B2.202"


def test_newly_pickable_lists_rows_whose_dependency_finished():
    done = DEPENDENT.replace(
        "| **B2.202** | [Base](#b2202-base) | Area | 📋 Planned | **Workhorse** | `b.py`, `tests/` |\n", ""
    )
    assert batch_backlog.newly_pickable(done, ["B2.201", "B2.204"]) == ["B2.201"]


def test_dry_run_marks_skipped_rows(tmp_path, capsys):
    backlog = tmp_path / "BACKLOG.md"
    backlog.write_text(DEPENDENT, encoding="utf-8")
    with patch.object(batch_backlog, "git", return_value=""):
        batch_backlog.main(["--dry-run", "--backlog", str(backlog)])
    out = capsys.readouterr().out
    assert "B2.201" in out and "SKIPPED: waits on B2.203" in out


def test_usage_cost_prices_each_token_kind_by_model_family():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 1_000_000,
        "cache_read_input_tokens": 1_000_000,
        "cache_creation_input_tokens": 2_000_000,
        "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000, "ephemeral_5m_input_tokens": 1_000_000},
    }
    price_in, price_out = batch_backlog.PRICE_PER_MTOK["sonnet"]
    expected = price_in * (1 + 0.1 + 1.25 + 2.0) + price_out
    assert batch_backlog.usage_cost("claude-sonnet-5-5", usage) == pytest.approx(expected)
    assert batch_backlog.usage_cost("claude-opus-5-5", usage) > batch_backlog.usage_cost("claude-haiku-5-5", usage)
    assert batch_backlog.usage_cost("mystery", {}) == 0


def test_transcript_digest_estimates_cost_once_per_streamed_message(tmp_path):
    now = batch_backlog.now_utc()
    usage = {"input_tokens": 1_000_000, "output_tokens": 0}

    def row(message_id):
        message = {"id": message_id, "model": "claude-sonnet-5-5", "usage": usage, "content": [{"type": "text", "text": "x"}]}
        return json.dumps({"type": "assistant", "timestamp": now.isoformat(), "message": message})

    path = tmp_path / "s.jsonl"
    path.write_text("\n".join([row("m1"), row("m1"), row("m2")]), encoding="utf-8")
    digest = batch_backlog.transcript_digest(str(path), now)
    assert digest["cost"] == pytest.approx(2 * batch_backlog.PRICE_PER_MTOK["sonnet"][0])


def write_batch_state(run_dir, current, finished=0, ended=None):
    run_dir.mkdir(parents=True, exist_ok=True)
    batch_backlog.write_state(
        str(run_dir),
        {"current": current and {"id": current}, "finished": [{}] * finished, "ended": ended},
    )


def test_wait_returns_when_the_current_item_changes(tmp_path):
    write_batch_state(tmp_path, "B2.1")
    sleeps = []

    def sleep(sec):
        sleeps.append(sec)
        if len(sleeps) == 3:
            write_batch_state(tmp_path, "B2.2", finished=1)

    batch_backlog.wait_for_change(str(tmp_path), 7, sleep=sleep)
    assert sleeps == [7, 7, 7]


def test_wait_returns_when_the_batch_ends(tmp_path):
    write_batch_state(tmp_path, "B2.1")

    def sleep(sec):
        write_batch_state(tmp_path, None, finished=1, ended="now")

    batch_backlog.wait_for_change(str(tmp_path), 1, sleep=sleep)


def test_wait_does_not_block_on_an_ended_batch_or_no_batch(tmp_path):
    def never(sec):
        raise AssertionError("slept")

    write_batch_state(tmp_path / "done", None, ended="then")
    batch_backlog.wait_for_change(str(tmp_path / "done"), 1, sleep=never)
    batch_backlog.wait_for_change(str(tmp_path / "missing"), 1, sleep=never)


def test_brief_warns_off_heredocs_and_untouched_file_failures():
    prompt = batch_backlog.build_prompt("B2.1")
    assert "file-writing tool" in prompt and "heredoc" in prompt
    assert "did not touch" in prompt and "one line" in prompt


# --- the agy backend ----------------------------------------------------------------------------

AGY_MODELS = [
    "gemini-4-argon-medium",
    "gemini-4-argon-high",
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "claude-opus-5-5-low",
    "claude-opus-5-5-medium",
    "claude-sonnet-5-5-medium",
]


def run_agy(tmp_path, sessions, *extra):
    with patch.object(batch_backlog, "agy_model_ids", return_value=AGY_MODELS):
        return run_main(tmp_path, sessions, "--agent", "agy", "--cli", "agy", *extra)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Gemini 3.8 Flash", "gemini-3.8-flash-medium"),
        ("Gemini 4 Argon", "gemini-4-argon-medium"),
        ("Claude Sonnet 5", "claude-sonnet-5-5-medium"),
        ("Claude Opus 5.5", "claude-opus-5-5-medium"),
        ("Claude Fable 5.1", None),
        ("the athlete", None),
    ],
)
def test_agy_resolver_maps_guide_names_to_listed_ids(name, expected):
    assert batch_backlog.agy_resolver(AGY_MODELS)(name) == expected


def test_agy_tiers_take_the_first_name_agy_offers():
    models = batch_backlog.tier_models(BACKLOG, resolve=batch_backlog.agy_resolver(AGY_MODELS))
    assert models == {
        "planner": "claude-opus-5-5-medium",  # no Fable on agy, so the next name in the tier
        "frontier": "claude-opus-5-5-medium",
        "workhorse": "claude-sonnet-5-5-medium",
        "fast": "gemini-3.8-flash-medium",
    }


def test_agy_model_ids_reads_the_first_column():
    out = (
        "Fetching available models...\n"
        "gemini-4-argon-medium\tGemini 4 Argon (Medium)\n"
        "claude-opus-5-5-high\tClaude Opus 5.5 (High)\n"
    )
    done = subprocess.CompletedProcess(["agy", "models"], 0, stdout=out, stderr="")
    with patch("batch_backlog.subprocess.run", return_value=done):
        assert batch_backlog.agy_model_ids("agy") == ["gemini-4-argon-medium", "claude-opus-5-5-high"]


def test_agy_items_run_with_the_prompt_in_argv_and_log_tokens(tmp_path):
    sessions = [fake_session("BATCH_RESULT: done — one"), fake_session("BATCH_RESULT: blocked — two")]
    run, log = run_agy(tmp_path, sessions)
    assert run.call_count == 2 and "B2.101" in sessions[0].prompt and "B3.8" in sessions[1].prompt
    first = run.call_args_list[0].args[0]
    assert first[first.index("--model") + 1] == "claude-sonnet-5-5-medium"
    assert "--dangerously-skip-permissions" not in first
    assert "| B2.101 | claude-sonnet-5-5-medium | done |" in log and "42k tok" in log and "`conv-1`" in log
    assert "Agent `agy`" in log and "permission mode `settings`" in log
    (state_path,) = (tmp_path / "logs").glob("*/state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["agent"] == "agy" and state["agy_models"] == AGY_MODELS


def test_agy_skip_permission_mode_passes_the_flag(tmp_path):
    run, _ = run_agy(tmp_path, [fake_session("BATCH_RESULT: done — x")], "--permission-mode", "skip", "--limit", "1")
    assert "--dangerously-skip-permissions" in run.call_args.args[0]


def test_an_agy_denial_is_an_error_that_names_the_fix_and_stops_the_batch(tmp_path):
    denied = fake_session("BATCH_RESULT: done — x", denied=["RunCommand"])
    run, log = run_agy(tmp_path, [denied])
    assert run.call_count == 1
    assert "| error |" in log and "agy denied RunCommand" in log and "command(*)" in log


def test_an_agy_empty_response_is_an_error(tmp_path):
    run, log = run_agy(tmp_path, [fake_session("")])
    assert run.call_count == 1 and "empty response" in log


@pytest.mark.parametrize(
    "extra, message",
    [(("--permission-mode", "auto"), "settings` or `skip"), (("--max-budget-usd", "5"), "claude-only")],
    ids=["claude-permission-mode", "budget"],
)
def test_agy_rejects_claude_only_options(tmp_path, capsys, extra, message):
    backlog = tmp_path / "BACKLOG.md"
    backlog.write_text(BACKLOG, encoding="utf-8")
    with pytest.raises(SystemExit):
        batch_backlog.main(["--agent", "agy", "--dry-run", "--backlog", str(backlog), *extra])
    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    "env, on_path, expected",
    [
        ({"BACKLOG_AGENT": "agy"}, {"claude", "agy"}, "agy"),
        ({"BACKLOG_AGENT": "AGY "}, set(), "agy"),
        ({"BACKLOG_AGENT": "bogus"}, {"agy"}, "agy"),
        ({}, {"claude", "agy"}, "claude"),
        ({}, {"agy"}, "agy"),
        ({}, set(), "claude"),
        ({"BACKLOG_AGENT": "cursor"}, {"claude"}, "cursor"),
        ({}, {"agent"}, "cursor"),
        ({}, {"cursor-agent"}, "cursor"),
        ({}, {"cursor"}, "claude"),
    ],
    ids=[
        "env-wins",
        "env-normalized",
        "bad-env-ignored",
        "both-on-path",
        "agy-only",
        "neither",
        "env-cursor",
        "cursor-agent-binary",
        "cursor-legacy-binary",
        "cursor-editor-is-not-the-agent",
    ],
)
def test_default_agent(env, on_path, expected):
    assert batch_backlog.default_agent(env, lambda name: name if name in on_path else None) == expected


def test_status_for_an_agy_batch_says_the_conversation_is_not_readable_live(tmp_path):
    run_dir = tmp_path / "20261106-0100"
    run_dir.mkdir()
    now = batch_backlog.now_utc().isoformat()
    state = {
        "stamp": "20261106-0100",
        "pid": 0,
        "agent": "agy",
        "agy_models": AGY_MODELS,
        "started": now,
        "base": "",
        "log": "",
        "model": "",
        "tier_models": [],
        "only": [],
        "include_human": False,
        "instructions": "",
        "current": {"id": "B2.101", "model": "gemini-3.8-flash-medium", "session": "", "started": now},
        "finished": [
            {"id": "B1.1", "model": "m", "result": "done", "summary": "s", "commits": [], "cost": None, "tokens": 9000}
        ],
        "ended": None,
        "stop_reason": "",
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    backlog = tmp_path / "BACKLOG.md"
    backlog.write_text(BACKLOG, encoding="utf-8")
    with patch.object(batch_backlog, "git", return_value=""), patch.object(batch_backlog, "check", return_value=([], None)):
        view = batch_backlog.render_status(str(run_dir), str(backlog))
    assert "· agy ·" in view and "9k tok" in view
    assert "agy session known when it ends" in view and "can't be read live" in view
    assert "B3.8 claude-opus-5-5-medium" in view


# --- the cursor backend -------------------------------------------------------------------------

CURSOR_MODELS = [
    "auto",
    "composer-2",
    "claude-sonnet-5",
    "claude-sonnet-5-thinking",
    "claude-opus-5-5",
    "claude-opus-4-8",
    "gemini-3.8-flash",
    "gpt-5.5",
    "sonnet-4-thinking",
]


def run_cursor(tmp_path, sessions, *extra):
    with patch.object(batch_backlog, "cursor_model_ids", return_value=CURSOR_MODELS):
        return run_main(tmp_path, sessions, "--agent", "cursor", "--cli", "agent", *extra)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Gemini 3.8 Flash", "gemini-3.8-flash"),
        ("Claude Sonnet 5", "claude-sonnet-5"),  # the exact id beats the -thinking variant
        ("Claude Opus 5.5", "claude-opus-5-5"),
        ("Claude Opus 5", "claude-opus-5-5"),
        ("Claude Opus 4", "claude-opus-4-8"),
        ("Claude Sonnet 4", "sonnet-4-thinking"),  # a short-form id still matches by family
        ("Auto", "auto"),
        ("Claude Fable 5.1", None),
        ("the athlete", None),
    ],
)
def test_cursor_resolver_maps_guide_names_to_listed_ids(name, expected):
    assert batch_backlog.cursor_resolver(CURSOR_MODELS)(name) == expected


def test_cursor_model_ids_reads_id_shaped_first_words():
    out = (
        "\x1b[1mAvailable models\x1b[0m\n\n"
        "auto - Auto  (current)\n"
        "  claude-sonnet-5-thinking - Claude Sonnet 5 Thinking\n"
        "claude-opus-5-5 - Claude Opus 5.5 [2m(current, default)[0m\n"
        "Tip: use --model <id> to switch\n"
    )
    done = subprocess.CompletedProcess(["agent", "models"], 0, stdout=out, stderr="")
    with patch("batch_backlog.subprocess.run", return_value=done):
        assert batch_backlog.cursor_model_ids("agent") == ["auto", "claude-sonnet-5-thinking", "claude-opus-5-5"]


def test_cursor_items_stream_with_the_prompt_on_stdin_and_force(tmp_path):
    sessions = [fake_session("BATCH_RESULT: done — one"), fake_session("BATCH_RESULT: blocked — two")]
    run, log = run_cursor(tmp_path, sessions, "--tier-model", "workhorse=auto")
    assert run.call_count == 2 and "B2.101" in sessions[0].prompt and "B3.8" in sessions[1].prompt
    first, second = (call.args[0] for call in run.call_args_list)
    assert first[:6] == ["agent", "-p", "--output-format", "stream-json", "--force", "--trust"]
    assert first[first.index("--model") + 1] == "auto"
    assert second[second.index("--model") + 1] == "claude-opus-5-5"
    assert "| B2.101 | auto | done |" in log and "| B3.8 | claude-opus-5-5 | blocked |" in log and "`chat-1`" in log
    assert "55k tok" in log
    assert "Agent `cursor`" in log and "permission mode `force`" in log
    assert list((tmp_path / "logs").glob("*/B2.101.jsonl"))
    (state_path,) = (tmp_path / "logs").glob("*/state.json")
    assert json.loads(state_path.read_text(encoding="utf-8"))["cursor_models"] == CURSOR_MODELS


def test_a_cursor_failure_without_a_result_event_stops_the_batch(tmp_path):
    run, log = run_cursor(tmp_path, [fake_session(None, returncode=1, stderr="Error: not logged in")])
    assert run.call_count == 1
    assert "| B2.101 | claude-sonnet-5 | error |" in log and "without a result event" in log


def test_a_lingering_cursor_session_is_ended_once_its_result_line_sits_quiet(tmp_path):
    lingering = fake_session("BATCH_RESULT: done — added it", hang=True, commits=["a1 feat: x (B2.101)"])
    run, log = run_cursor(tmp_path, [lingering, fake_session("BATCH_RESULT: blocked — two")])
    assert lingering.ended and run.call_count == 2
    assert "| B2.101 | claude-sonnet-5 | done |" in log and "added it" in log


def test_a_cursor_tool_call_after_the_result_line_clears_the_hold(tmp_path):
    path = tmp_path / "s.jsonl"
    said = {"type": "assistant", "message": {"content": [{"type": "text", "text": "BATCH_RESULT: done — x"}]}}
    call = {"type": "tool_call", "subtype": "started", "tool_call": {"shellToolCall": {"args": {"command": "git status"}}}}
    path.write_text(json.dumps(said) + "\n", encoding="utf-8")
    later = batch_backlog.now_utc() + timedelta(minutes=10)
    assert batch_backlog.stream_held_result(str(path), later, 3) == "BATCH_RESULT: done — x"
    assert batch_backlog.stream_held_result(str(path), batch_backlog.now_utc(), 3) is None  # not quiet yet
    path.write_text(json.dumps(said) + "\n" + json.dumps(call) + "\n", encoding="utf-8")
    assert batch_backlog.stream_held_result(str(path), later, 3) is None


@pytest.mark.parametrize(
    "extra, message",
    [(("--permission-mode", "auto"), "is `force`"), (("--max-budget-usd", "5"), "claude-only")],
    ids=["other-permission-mode", "budget"],
)
def test_cursor_rejects_options_it_cannot_honor(tmp_path, capsys, extra, message):
    backlog = tmp_path / "BACKLOG.md"
    backlog.write_text(BACKLOG, encoding="utf-8")
    with pytest.raises(SystemExit):
        batch_backlog.main(["--agent", "cursor", "--dry-run", "--backlog", str(backlog), *extra])
    assert message in capsys.readouterr().err


def test_status_for_a_cursor_batch_reads_its_event_stream(tmp_path):
    run_dir = tmp_path / "20261106-0100"
    run_dir.mkdir()
    now = batch_backlog.now_utc().isoformat()
    state = {
        "stamp": "20261106-0100",
        "pid": 0,
        "agent": "cursor",
        "cursor_models": CURSOR_MODELS,
        "started": now,
        "base": "",
        "log": "",
        "model": "",
        "tier_models": [],
        "only": [],
        "include_human": False,
        "instructions": "",
        "current": {"id": "B2.101", "model": "claude-sonnet-5", "session": "", "started": now},
        "finished": [],
        "ended": None,
        "stop_reason": "",
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    events = [
        {"type": "system", "subtype": "init", "session_id": "chat-9"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Reading the entry."}]}},
        {"type": "tool_call", "subtype": "started", "tool_call": {"readToolCall": {"args": {"path": "BACKLOG.md"}}}},
        {"type": "tool_call", "subtype": "completed", "tool_call": {"readToolCall": {"result": {"success": {}}}}},
        {"type": "tool_call", "subtype": "started", "tool_call": {"shellToolCall": {"args": {"command": "pytest -q"}}}},
        {"type": "tool_call", "subtype": "completed", "tool_call": {"shellToolCall": {"result": {"rejected": {}}}}},
        {"type": "tool_call", "subtype": "started", "tool_call": {"tool": {"case": "grepToolCall", "value": {"args": {"pattern": "TODO"}}}}},
        {"type": "tool_call", "subtype": "completed", "tool_call": {"grepToolCall": {"result": {"permissionDenied": {}}}}},
    ]
    (run_dir / "B2.101.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    backlog = tmp_path / "BACKLOG.md"
    backlog.write_text(BACKLOG, encoding="utf-8")
    with patch.object(batch_backlog, "git", return_value=""), patch.object(batch_backlog, "check", return_value=([], None)):
        view = batch_backlog.render_status(str(run_dir), str(backlog))
    assert "· cursor ·" in view and "cursor session chat-9" in view
    assert "1 turns, 3 tool calls" in view and "> Read BACKLOG.md" in view and "> Shell pytest -q" in view
    assert "> Grep TODO" in view and "Rejected or denied so far: 2" in view and "Last said: Reading the entry." in view
    assert "B3.8 claude-opus-5-5" in view
