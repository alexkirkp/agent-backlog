#!/usr/bin/env python3
"""Check that BACKLOG.md and COMPLETED.md follow their authoring conventions.

Agents and humans edit the backlog concurrently, and the conventions drift unless something
checks them. This script fails (exit 1) when:

* a summary-table row has no body entry, a body entry has no row, or a table row is malformed;
* body entries are not in the same order as the table (the table is the work queue);
* an entry body runs over the line cap (default 45 non-blank lines);
* an entry sits under a `## Tier N` section that doesn't match its `TN.x` ID;
* an ID appears twice, was retired via `(formerly ...)`, or is already closed in COMPLETED.md;
* a table link's anchor doesn't match its entry heading;
* an entry has no `**Status:**` line, its status glyph disagrees with the table, or it is marked ✅ in BACKLOG.md;
* COMPLETED.md has non-sequential `## N.` numbers, missing `(TN.x)` IDs, or missing/broken `§N` index links;
* a link to `spikes/...` points to a missing file.

Usage: python tools/check_backlog.py [DIR] [--max-lines N]
Standard library only, Python 3.8+.
"""

import argparse
import os
import re
import sys

TASK_ID_RE = re.compile(r"\bT(\d+)\.(\d+)[a-z]?\b")
TASK_ID_CI_RE = re.compile(r"\bT(\d+)\.(\d+)([a-z])?\b", re.IGNORECASE)
ENTRY_HEADING = re.compile(r"^### (T(\d+)\.\d+[a-z]?)\.\s+(.*)$")
TIER_HEADING = re.compile(r"^## Tier (\d+)\b")
TABLE_ROW = re.compile(
    r"^\|\s*\*\*(T\d+\.\d+[a-z]?)\*\*\s*\|\s*\[(?:(?!\]\(#)[^|])+\]\(#([^)|]+)\)\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]+)\|\s*$"
)
TABLE_SEPARATOR = re.compile(r"^\|[\s:\-|]+\|?\s*$")
STATUS_LINE_RE = re.compile(r"^\s*(?:[*+-]\s+)?\*\*Status:\*\*\s*(.*)$")
COMPLETED_HEADING = re.compile(r"^## (\d+)\.\s+(.*)$")
COMPLETED_INDEX_LINK = re.compile(r"\[§(\d+)\]\(#([^)]+)\)")
FORMERLY_CLAUSE_RE = re.compile(
    r"(?<!`)\bformerly\s+`?(T\d+\.\d+[a-z]?)\b`?(?:\s*(?:,\s*(?:(?:and|or)\b\s*)?|/|\b(?:and|or)\b\s*)\s*`?(T\d+\.\d+[a-z]?)\b`?)*",
    re.IGNORECASE,
)
SPIKE_LINK_RE = re.compile(r"\]\((?:\./)?(spikes/[^)#\s]+)")
FENCE_RE = re.compile(r"^(`{3,})(?!.*`)|^(~{3,})")
INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)")
CODE_TASK_ID_RE = re.compile(r"^`+\s*T\d+\.\d+[a-z]?\s*`+$", re.IGNORECASE)

STATUS_GLYPHS = ("📋", "🚧", "💡", "⏸️", "✅")


def extract_status_glyph(text):
    best_pos = None
    best_glyph = None
    for glyph in STATUS_GLYPHS:
        pos = text.find(glyph)
        if pos != -1 and (best_pos is None or pos < best_pos):
            best_pos = pos
            best_glyph = glyph
    pos_pause = text.find("⏸")
    if pos_pause != -1 and (best_pos is None or pos_pause < best_pos):
        best_glyph = "⏸️"
    return best_glyph


def is_completed_status(glyph, status_raw):
    if glyph == "✅":
        return True
    head = re.split(r"[(·—–:;]|\s+-\s+", status_raw, maxsplit=1)[0].lower()
    return bool(re.search(r"\bcompleted\b", head))


def strip_non_id_inline_code(line):
    return INLINE_CODE_RE.sub(
        lambda m: m.group(0) if CODE_TASK_ID_RE.match(m.group(0)) else "",
        line,
    )


def extract_formerly_ids(line):
    ids = []
    cleaned = strip_non_id_inline_code(line)
    for clause in FORMERLY_CLAUSE_RE.finditer(cleaned):
        for m in TASK_ID_CI_RE.finditer(clause.group(0)):
            suffix = (m.group(3) or "").lower()
            ids.append(f"T{m.group(1)}.{m.group(2)}{suffix}")
    return ids


def strip_formerly_clauses(text):
    cleaned = FORMERLY_CLAUSE_RE.sub("", strip_non_id_inline_code(text))
    return re.sub(r"\([\s,;:/]*\)\s*$", "", cleaned).strip()


def base_task_id(task_id):
    return task_id[:-1] if task_id and task_id[-1].isalpha() else task_id


def github_slug(heading):
    """GitHub's anchor for a heading: lowercase, punctuation dropped, spaces to hyphens."""
    slug = heading.strip().lower()
    slug = re.sub(r"[^\w\- ]", "", slug)
    return slug.replace(" ", "-")


def strip_html_comments(line, in_comment):
    out = []
    i = 0
    n = len(line)
    while i < n:
        if in_comment:
            end = line.find("-->", i)
            if end == -1:
                return "".join(out), True
            i = end + 3
            in_comment = False
        else:
            if line.startswith("<!--", i):
                in_comment = True
                i += 4
            elif line[i] == "`":
                ticks = 1
                while i + ticks < n and line[i + ticks] == "`":
                    ticks += 1
                marker = "`" * ticks
                close = line.find(marker, i + ticks)
                if close != -1:
                    out.append(line[i : close + ticks])
                    i = close + ticks
                else:
                    out.append(marker)
                    i += ticks
            else:
                out.append(line[i])
                i += 1
    return "".join(out), in_comment


def markdown_lines(text, include_fences=False):
    """(lineno, line) pairs outside fenced code blocks and HTML comments, so templates are skipped."""
    fence_marker = None
    in_comment = False
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if fence_marker is not None:
            if stripped.startswith(fence_marker) and not stripped.lstrip(fence_marker[0]).strip():
                fence_marker = None
            if include_fences:
                yield lineno, line, True
            continue
        if in_comment or "<!--" in line:
            line, in_comment = strip_html_comments(line, in_comment)
            stripped = line.strip()
            if not stripped:
                continue
        fence_match = FENCE_RE.match(stripped)
        if fence_match:
            fence_marker = fence_match.group(1) or fence_match.group(2)
            if include_fences:
                yield lineno, line, True
            continue
        if include_fences:
            yield lineno, line, False
        else:
            yield lineno, line


def parse_backlog(text):
    rows = []  # dicts in table order
    malformed_rows = []  # (lineno, line)
    malformed_headings = []  # (lineno, line)
    entries = []  # dicts in body order
    formerly_ids = []  # (id, source_id, lineno)
    spike_links = []  # (lineno, path)
    section = None
    tier = None
    current = None

    for lineno, line, in_fence in markdown_lines(text, include_fences=True):
        if in_fence:
            if current and line.strip():
                current["body"] += 1
            continue

        line_no_code = INLINE_CODE_RE.sub("", line)
        for m in SPIKE_LINK_RE.finditer(line_no_code):
            spike_links.append((lineno, m.group(1)))

        if tier is None:
            row = TABLE_ROW.match(line)
            if row:
                row_id = row.group(1)
                rows.append(
                    {
                        "id": row_id,
                        "anchor": row.group(2),
                        "status": extract_status_glyph(row.group(4)),
                        "status_raw": row.group(4).strip(),
                        "line": lineno,
                    }
                )
                for fid in extract_formerly_ids(line):
                    formerly_ids.append((fid, row_id, lineno))
                continue

        if (
            section == "Active Engineering Backlog"
            and line.lstrip().startswith("|")
            and not TABLE_SEPARATOR.match(line)
            and not re.match(r"^\|\s*ID\s*\|", line)
        ):
            malformed_rows.append((lineno, line.strip()))
            continue

        heading = ENTRY_HEADING.match(line)
        if heading:
            entry_id = heading.group(1)
            current = {
                "id": entry_id,
                "tier": int(heading.group(2)),
                "section_tier": tier,
                "slug": github_slug(line[4:]),
                "line": lineno,
                "body": 0,
                "status": False,
                "status_glyph": None,
                "status_raw": "",
            }
            entries.append(current)
            for fid in extract_formerly_ids(line):
                formerly_ids.append((fid, entry_id, lineno))
            continue

        if line.startswith("## "):
            if re.match(r"^##\s+T\d+", line):
                malformed_headings.append((lineno, line.strip()))
                current = None
                continue
            section = line[3:].strip()
            tier_match = TIER_HEADING.match(line)
            tier = int(tier_match.group(1)) if tier_match else None
            current = None
            continue

        if line.startswith("### ") or re.match(r"^#{4,}\s+", line):
            if (
                line.startswith("### ")
                and (tier is not None or section == "Active Engineering Backlog")
            ) or re.match(r"^#{3,}\s+T\d+", line):
                malformed_headings.append((lineno, line.strip()))
                current = None
                continue
            if not line.startswith("### ") and current:
                current["body"] += 1
                continue
            current = None
            continue

        if current and line.strip() and line.strip() != "---":
            current["body"] += 1
            status_match = STATUS_LINE_RE.match(line)
            if status_match and not current["status"]:
                status_val = status_match.group(1).strip()
                current["status"] = True
                current["status_glyph"] = extract_status_glyph(status_val)
                current["status_raw"] = status_val
                for fid in extract_formerly_ids(line):
                    formerly_ids.append((fid, current["id"], lineno))

    return rows, malformed_rows, malformed_headings, entries, formerly_ids, spike_links


def parse_completed(text):
    milestones = []  # dicts in file order
    malformed_headings = []  # (lineno, line)
    index_links = {}  # num -> (anchor, lineno)
    duplicate_index_nums = []
    formerly_ids = []  # (id, ms_num, lineno)
    spike_links = []  # (lineno, path)
    in_index = False
    current_ms = None
    seen_ms_status = False

    for lineno, line in markdown_lines(text):
        line_no_code = INLINE_CODE_RE.sub("", line)
        for m in SPIKE_LINK_RE.finditer(line_no_code):
            spike_links.append((lineno, m.group(1)))

        if line.startswith("## "):
            heading_text = line[3:].strip()
            if heading_text == "Index of Delivered Milestones":
                in_index = True
                current_ms = None
                seen_ms_status = False
                continue
            in_index = False
            seen_ms_status = False
            m = COMPLETED_HEADING.match(line)
            if m:
                num = int(m.group(1))
                rest = strip_formerly_clauses(m.group(2).strip())
                paren_match = re.search(r"\(([^()]+)\)\s*$", rest)
                closed_ids = (
                    [im.group(0) for im in TASK_ID_RE.finditer(paren_match.group(1))]
                    if paren_match
                    else []
                )
                current_ms = {
                    "num": num,
                    "slug": github_slug(heading_text),
                    "ids": closed_ids,
                    "line": lineno,
                }
                milestones.append(current_ms)
                for fid in extract_formerly_ids(line):
                    formerly_ids.append((fid, num, lineno))
            else:
                current_ms = None
                malformed_headings.append((lineno, line.strip()))
            continue

        if re.match(r"^#{3,}\s+", line):
            if in_index or re.match(r"^#{3,}\s+(?:§?\d+\.|T\d+)", line):
                in_index = False
                current_ms = None
                malformed_headings.append((lineno, line.strip()))
                continue

        if current_ms and not seen_ms_status and STATUS_LINE_RE.match(line):
            seen_ms_status = True
            for fid in extract_formerly_ids(line):
                formerly_ids.append((fid, current_ms["num"], lineno))

        if in_index:
            for im in COMPLETED_INDEX_LINK.finditer(line):
                num = int(im.group(1))
                if num in index_links:
                    duplicate_index_nums.append(num)
                index_links[num] = (im.group(2), lineno)

    return (
        milestones,
        malformed_headings,
        index_links,
        duplicate_index_nums,
        formerly_ids,
        spike_links,
    )


def next_available_summary(entries, completed_milestones, formerly_ids, tiers=(0, 1, 2, 3)):
    max_per_tier = {t: 0 for t in tiers}
    all_ids = [e["id"] for e in entries]
    for ms in completed_milestones:
        all_ids.extend(ms["ids"])
    all_ids.extend(fid for fid, *_ in formerly_ids)

    for task_id in all_ids:
        m = TASK_ID_RE.match(task_id)
        if m:
            tier, num = int(m.group(1)), int(m.group(2))
            if tier in max_per_tier and num > max_per_tier[tier]:
                max_per_tier[tier] = num

    next_ids = [f"T{t}.{max_per_tier[t] + 1}" for t in tiers]
    next_sec = max((ms["num"] for ms in completed_milestones), default=0) + 1
    return next_ids, next_sec


def check(backlog_dir, max_lines):
    backlog_path = os.path.join(backlog_dir, "BACKLOG.md")
    with open(backlog_path, encoding="utf-8") as f:
        (
            rows,
            malformed_rows,
            malformed_headings,
            entries,
            backlog_formerly,
            backlog_spikes,
        ) = parse_backlog(f.read())

    completed_milestones = []
    completed_malformed_headings = []
    index_links = {}
    duplicate_index_nums = []
    completed_formerly = []
    completed_spikes = []
    completed_path = os.path.join(backlog_dir, "COMPLETED.md")
    if os.path.exists(completed_path):
        with open(completed_path, encoding="utf-8") as f:
            (
                completed_milestones,
                completed_malformed_headings,
                index_links,
                duplicate_index_nums,
                completed_formerly,
                completed_spikes,
            ) = parse_completed(f.read())

    problems = []

    for lineno, raw in malformed_rows:
        problems.append(
            f"BACKLOG.md (line {lineno}): malformed summary-table row `{raw}` "
            "(expected `| **TN.x** | [Title](#anchor) | Focus | Status | Model | Surface |`)"
        )

    for lineno, raw in malformed_headings:
        problems.append(
            f"BACKLOG.md (line {lineno}): malformed entry heading `{raw}` "
            "(expected `### TN.x. Title`)"
        )

    for lineno, raw in completed_malformed_headings:
        problems.append(
            f"COMPLETED.md (line {lineno}): malformed milestone heading `{raw}` "
            "(expected `## N. Title (TN.x)`)"
        )

    row_ids = [r["id"] for r in rows]
    entry_ids = [e["id"] for e in entries]
    by_id = {e["id"]: e for e in entries}
    row_by_id = {r["id"]: r for r in rows}

    for ids, where in ((row_ids, "summary table"), (entry_ids, "entry headings")):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            problems.append(f"duplicate IDs in the {where}: {', '.join(dupes)}")

    for missing in [i for i in row_ids if i not in by_id]:
        problems.append(f"{missing}: has a table row but no `### {missing}.` entry")
    for missing in [i for i in entry_ids if i not in row_ids]:
        problems.append(f"{missing}: has an entry but no summary-table row")

    shared = [i for i in row_ids if i in by_id]
    body_order = [i for i in entry_ids if i in row_ids]
    if shared != body_order:
        problems.append(
            "entry bodies are not in table order (the table is the work queue)\n"
            f"    table: {', '.join(shared)}\n    body:  {', '.join(body_order)}"
        )

    for r in rows:
        entry_id = r["id"]
        entry = by_id.get(entry_id)
        if is_completed_status(r["status"], r["status_raw"]):
            problems.append(
                f"{entry_id} (line {r['line']}): table row is marked Completed in BACKLOG.md; "
                "move completed items to COMPLETED.md"
            )
        elif not r["status"]:
            problems.append(
                f"{entry_id} (line {r['line']}): table row has no valid status glyph "
                "(expected one of 📋, 🚧, 💡, ⏸️)"
            )
        if entry and r["anchor"] != entry["slug"]:
            problems.append(
                f"{entry_id}: table links #{r['anchor']} but the heading's anchor is #{entry['slug']}"
            )

    ms_ids_by_num = {ms["num"]: ms["ids"] for ms in completed_milestones}
    retired_formerly = {}
    retired_sources = {}
    for fid, source_id, lineno in backlog_formerly:
        retired_formerly[fid] = f"retired by {source_id} (BACKLOG.md line {lineno})"
        retired_sources.setdefault(fid, set()).add((source_id, "backlog"))
    for fid, ms_num, lineno in completed_formerly:
        retired_formerly.setdefault(fid, f"retired in COMPLETED.md (line {lineno})")
        origin = f"§{ms_num}" if ms_num is not None else "COMPLETED.md"
        source_ids = ms_ids_by_num.get(ms_num) or [origin]
        for sid in source_ids:
            retired_sources.setdefault(fid, set()).add((sid, origin))

    completed_ids = {}
    for ms in completed_milestones:
        for cid in ms["ids"]:
            external_sources = [
                src
                for src, origin in retired_sources.get(cid, ())
                if origin != f"§{ms['num']}"
            ]
            if cid in completed_ids:
                problems.append(
                    f"COMPLETED.md (line {ms['line']}): ID {cid} is closed in both §{completed_ids[cid]} and §{ms['num']}"
                )
            elif cid in retired_formerly and any(
                base_task_id(src) != cid for src in external_sources
            ):
                problems.append(
                    f"COMPLETED.md (line {ms['line']}): ID {cid} in §{ms['num']} reuses retired ID {cid} ({retired_formerly[cid]})"
                )
            completed_ids[cid] = ms["num"]

    completed_base_ids = {
        cid[:-1]: (cid, sec)
        for cid, sec in completed_ids.items()
        if cid[-1].isalpha()
    }

    retired_base_ids = {}
    for fid, reason in retired_formerly.items():
        if fid[-1].isalpha():
            retired_base_ids.setdefault(fid[:-1], []).append((fid, reason))

    active_base_ids = {
        e["id"][:-1]: e["id"] for e in entries if e["id"][-1].isalpha()
    }

    for e in entries:
        where = f"{e['id']} (line {e['line']})"
        base_id = e["id"][:-1] if e["id"][-1].isalpha() else None
        if e["body"] > max_lines:
            problems.append(
                f"{where}: body is {e['body']} non-blank lines (cap {max_lines}); "
                "move design notes to spikes/<id>_<slug>.md or split the entry"
            )
        if e["section_tier"] is None:
            problems.append(f"{where}: sits outside any `## Tier N` section")
        elif e["section_tier"] != e["tier"]:
            problems.append(
                f"{where}: is under Tier {e['section_tier']} but its ID says Tier {e['tier']}; "
                "move it or renumber it (keep the old ID in the status line)"
            )
        if not e["status"]:
            problems.append(f"{where}: has no `**Status:**` line")
        else:
            if is_completed_status(e["status_glyph"], e["status_raw"]):
                problems.append(
                    f"{where}: is marked Completed in BACKLOG.md; move it to COMPLETED.md"
                )
            elif not e["status_glyph"]:
                problems.append(
                    f"{where}: `**Status:**` line has no valid status glyph (expected one of 📋, 🚧, 💡, ⏸️)"
                )
            row = row_by_id.get(e["id"])
            if (
                row
                and row["status"]
                and e["status_glyph"]
                and row["status"] != e["status_glyph"]
            ):
                problems.append(
                    f"{where}: table status is {row['status']} but entry status is {e['status_glyph']}"
                )
        retired_sibling = (
            next(
                (
                    (chunk_id, reason)
                    for chunk_id, reason in retired_base_ids.get(base_id, ())
                    if any(
                        base_task_id(src) != base_id
                        for src, _ in retired_sources.get(chunk_id, ())
                    )
                ),
                None,
            )
            if base_id
            else None
        )
        if e["id"] in completed_ids:
            problems.append(
                f"{where}: is already closed in COMPLETED.md (§{completed_ids[e['id']]}); "
                "remove it from BACKLOG.md"
            )
        elif base_id and base_id in completed_ids:
            problems.append(
                f"{where}: parent ID {base_id} is already closed in COMPLETED.md (§{completed_ids[base_id]}); "
                "remove it from BACKLOG.md"
            )
        elif not base_id and e["id"] in completed_base_ids:
            chunk_id, sec = completed_base_ids[e["id"]]
            problems.append(
                f"{where}: chunk {chunk_id} is already closed in COMPLETED.md (§{sec}); "
                f"cannot use unlettered {e['id']} in BACKLOG.md"
            )
        elif e["id"] in retired_formerly:
            problems.append(
                f"{where}: reuses retired ID {e['id']} ({retired_formerly[e['id']]})"
            )
        elif (
            base_id
            and base_id in retired_formerly
            and any(base_task_id(src) != base_id for src, _ in retired_sources.get(base_id, ()))
        ):
            problems.append(
                f"{where}: parent ID {base_id} was retired ({retired_formerly[base_id]})"
            )
        elif not base_id and e["id"] in retired_base_ids:
            chunk_id, reason = retired_base_ids[e["id"]][0]
            problems.append(
                f"{where}: chunk {chunk_id} was retired ({reason}); "
                f"cannot use unlettered {e['id']} in BACKLOG.md"
            )
        elif retired_sibling:
            chunk_id, reason = retired_sibling
            problems.append(
                f"{where}: sibling chunk {chunk_id} was retired ({reason}); "
                "re-tier chunks together"
            )
        if not base_id and e["id"] in active_base_ids:
            problems.append(
                f"{where}: unlettered ID {e['id']} coexists with lettered chunk {active_base_ids[e['id']]}"
            )

    for dup_num in sorted(set(duplicate_index_nums)):
        problems.append(f"COMPLETED.md: duplicate index entry for §{dup_num}")

    for idx, ms in enumerate(completed_milestones, 1):
        where = f"COMPLETED.md §{ms['num']} (line {ms['line']})"
        if ms["num"] != idx:
            problems.append(
                f"{where}: expected milestone number §{idx} (entries are numbered sequentially 1, 2, …)"
            )
        if not ms["ids"]:
            problems.append(f"{where}: heading has no `(TN.x)` backlog ID at the end")
        if ms["num"] not in index_links:
            problems.append(
                f"{where}: has no `[§{ms['num']}](#{ms['slug']})` line in `## Index of Delivered Milestones`"
            )
        else:
            idx_anchor, idx_line = index_links[ms["num"]]
            if idx_anchor != ms["slug"]:
                problems.append(
                    f"COMPLETED.md (line {idx_line}): index for §{ms['num']} links #{idx_anchor} "
                    f"but heading anchor is #{ms['slug']}"
                )

    milestone_nums = {ms["num"] for ms in completed_milestones}
    for num, (_, idx_line) in sorted(index_links.items()):
        if num not in milestone_nums:
            problems.append(
                f"COMPLETED.md (line {idx_line}): index references §{num} but no `## {num}.` entry exists"
            )

    for src_file, links in (("BACKLOG.md", backlog_spikes), ("COMPLETED.md", completed_spikes)):
        for lineno, rel_path in links:
            full_path = os.path.join(backlog_dir, *rel_path.split("/"))
            if not os.path.isfile(full_path):
                problems.append(
                    f"{src_file} (line {lineno}): spike link `{rel_path}` does not exist"
                )

    all_formerly = [
        (fid, lineno) for fid, _, lineno in backlog_formerly
    ] + [(fid, lineno) for fid, _, lineno in completed_formerly]
    summary = next_available_summary(entries, completed_milestones, all_formerly)
    return problems, len(entries), summary


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "backlog_dir",
        nargs="?",
        default=".",
        help="folder holding BACKLOG.md (default: current folder)",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=45,
        help="entry body cap in non-blank lines (default: 45)",
    )
    args = parser.parse_args(argv)

    if not os.path.exists(os.path.join(args.backlog_dir, "BACKLOG.md")):
        print(f"no BACKLOG.md in {args.backlog_dir!r}", file=sys.stderr)
        return 2

    problems, count, (next_ids, next_sec) = check(args.backlog_dir, args.max_lines)
    if problems:
        print(f"backlog check failed ({len(problems)} problem{'s' if len(problems) != 1 else ''}):")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"backlog OK: {count} entr{'ies' if count != 1 else 'y'} "
        f"(next IDs: {', '.join(next_ids)}; next completed: §{next_sec})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
