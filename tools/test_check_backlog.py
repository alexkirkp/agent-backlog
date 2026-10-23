#!/usr/bin/env python3
"""Unit tests for tools/check_backlog.py."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import check_backlog


VALID_BACKLOG = """\
# Development Backlog

## Active Engineering Backlog

| ID | Task Name | Focus Area | Status | Model Fit | Target Surface / Script |
|:---:|---|---|:---:|:---:|---|
| **B0.1** | [First Task](#b01-first-task) | Hygiene | 📋 Planned | **Workhorse** | `tools/` |
| **B1.2** | [Second Task](#b12-second-task) | Core | 🚧 In Progress | **Frontier** | `src/` |

## Tier 0: Repository Hygiene & Blockers

### B0.1. First Task
* **Status:** 📋 **Planned**
* **Why it matters:** test.

## Tier 1: High Priority

### B1.2. Second Task
* **Status:** 🚧 **In Progress** (formerly B2.3)
* **Why it matters:** test.
"""

VALID_COMPLETED = """\
# Completed Milestones & Engineering Record

## Index of Delivered Milestones

* **Initial Setup ([§1](#1-initial-setup-b11)):** bootstrapped repo.

---

## 1. Initial Setup (B1.1)

* **Status:** ✅ **Completed (2026-10-01)**
"""


class CheckBacklogTests(unittest.TestCase):
    def run_check(self, backlog_text, completed_text=VALID_COMPLETED, max_lines=45, extra_files=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "BACKLOG.md"), "w", encoding="utf-8") as f:
                f.write(backlog_text)
            if completed_text is not None:
                with open(os.path.join(tmpdir, "COMPLETED.md"), "w", encoding="utf-8") as f:
                    f.write(completed_text)
            for rel_path, content in (extra_files or {}).items():
                full_path = os.path.join(tmpdir, *rel_path.split("/"))
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
            return check_backlog.check(tmpdir, max_lines)

    def test_valid_backlog_and_next_ids(self):
        problems, count, (next_ids, next_sec) = self.run_check(VALID_BACKLOG)
        self.assertEqual(problems, [])
        self.assertEqual(count, 2)
        self.assertEqual(next_ids, ["B0.2", "B1.3", "B2.4", "B3.1"])
        self.assertEqual(next_sec, 2)

    def test_single_line_comments_and_nested_fences_ignored(self):
        text = (
            VALID_BACKLOG
            + "\n<!-- ### B9.9. Commented Heading -->\n"
            + "````markdown\n```markdown\n### B8.8. Nested Fence Heading\n```\n````\n"
        )
        problems, count, _ = self.run_check(text)
        self.assertEqual(problems, [])
        self.assertEqual(count, 2)

    def test_malformed_table_row_detected(self):
        bad = VALID_BACKLOG.replace("| **B0.1** |", "| B0.1 |")
        problems, _, _ = self.run_check(bad)
        self.assertTrue(any("malformed summary-table row" in p for p in problems), problems)

    def test_status_mismatch_between_table_and_entry(self):
        bad = VALID_BACKLOG.replace("* **Status:** 🚧 **In Progress**", "* **Status:** 📋 **Planned**")
        problems, _, _ = self.run_check(bad)
        self.assertTrue(any("table status is 🚧 but entry status is 📋" in p for p in problems), problems)

    def test_completed_status_rejected_in_backlog(self):
        bad = VALID_BACKLOG.replace("📋 Planned", "✅ Completed").replace("📋 **Planned**", "✅ **Completed**")
        problems, _, _ = self.run_check(bad)
        self.assertTrue(any("marked Completed in BACKLOG.md" in p for p in problems), problems)

    def test_reused_formerly_id_rejected(self):
        bad = VALID_BACKLOG + (
            "\n## Tier 2: Medium\n\n### B2.3. Reused ID\n* **Status:** 📋 **Planned**\n"
        )
        bad = bad.replace(
            "| **B1.2** | [Second Task](#b12-second-task) | Core | 🚧 In Progress | **Frontier** | `src/` |",
            "| **B1.2** | [Second Task](#b12-second-task) | Core | 🚧 In Progress | **Frontier** | `src/` |\n"
            "| **B2.3** | [Reused ID](#b23-reused-id) | Core | 📋 Planned | **Workhorse** | `src/` |",
        )
        problems, _, _ = self.run_check(bad)
        self.assertTrue(any("reuses retired ID B2.3" in p for p in problems), problems)

    def test_completed_id_collision_and_multi_id_support(self):
        completed = """\
# Completed

## Index of Delivered Milestones

* **Batch ([§1](#1-batch-b01-b09)):** closed two items.

## 1. Batch (B0.1, B0.9)
"""
        problems, _, _ = self.run_check(VALID_BACKLOG, completed_text=completed)
        self.assertTrue(any("B0.1 (line" in p and "already closed in COMPLETED.md (§1)" in p for p in problems), problems)

    def test_completed_missing_index_and_sequence_checked(self):
        completed = """\
# Completed

## Index of Delivered Milestones

## 2. Out of Order (B1.1)
"""
        problems, _, _ = self.run_check(VALID_BACKLOG, completed_text=completed)
        self.assertTrue(any("expected milestone number §1" in p for p in problems), problems)
        self.assertTrue(any("has no `[§2]" in p for p in problems), problems)

    def test_spike_link_existence_checked(self):
        with_spike = VALID_BACKLOG + "* **Background:** see [spike](spikes/B1.2_design.md).\n"
        problems, _, _ = self.run_check(with_spike)
        self.assertTrue(any("spike link `spikes/B1.2_design.md` does not exist" in p for p in problems), problems)

        problems_ok, _, _ = self.run_check(
            with_spike, extra_files={"spikes/B1.2_design.md": "# Spike\n"}
        )
        self.assertEqual(problems_ok, [])

    def test_lettered_chunk_parent_collision(self):
        chunked = VALID_BACKLOG.replace("**B1.2**", "**B1.2a**").replace(
            "#b12-second-task", "#b12a-second-task"
        ).replace("### B1.2. Second Task", "### B1.2a. Second Task")
        completed_umbrella = """\
# Completed

## Index of Delivered Milestones

* **Umbrella ([§1](#1-umbrella-b12)):** all chunks done.

## 1. Umbrella (B1.2)
"""
        problems, _, _ = self.run_check(chunked, completed_text=completed_umbrella)
        self.assertTrue(any("parent ID B1.2 is already closed" in p for p in problems), problems)

    def test_unlettered_collision_with_completed_or_retired_chunk(self):
        completed_chunk = """\
# Completed

## Index of Delivered Milestones

* **Chunk A ([§1](#1-chunk-a-b12a)):** first chunk done.

## 1. Chunk A (B1.2a)
"""
        problems, _, _ = self.run_check(VALID_BACKLOG, completed_text=completed_chunk)
        self.assertTrue(
            any("chunk B1.2a is already closed in COMPLETED.md (§1)" in p for p in problems),
            problems,
        )

        retired_chunk = VALID_BACKLOG.replace("(formerly B2.3)", "(formerly B0.1a)")
        problems_ret, _, _ = self.run_check(retired_chunk)
        self.assertTrue(
            any("chunk B0.1a was retired" in p for p in problems_ret),
            problems_ret,
        )

    def test_missing_status_glyph_and_pause_variation_selector(self):
        no_table_glyph = VALID_BACKLOG.replace("📋 Planned", "Planned")
        problems, _, _ = self.run_check(no_table_glyph)
        self.assertTrue(any("table row has no valid status glyph" in p for p in problems), problems)

        no_entry_glyph = VALID_BACKLOG.replace("* **Status:** 📋 **Planned**", "* **Status:** **Planned**")
        problems_entry, _, _ = self.run_check(no_entry_glyph)
        self.assertTrue(
            any("`**Status:**` line has no valid status glyph" in p for p in problems_entry),
            problems_entry,
        )

        # Bare U+23F8 (⏸) in table and U+23F8 U+FE0F (⏸️) in entry should match cleanly
        pause_mixed = VALID_BACKLOG.replace("📋 Planned", "\u23f8 Gated").replace(
            "📋 **Planned**", "\u23f8\ufe0f **Gated**"
        )
        problems_pause, _, _ = self.run_check(pause_mixed)
        self.assertEqual(problems_pause, [])

    def test_malformed_headings_detected(self):
        bad_backlog = VALID_BACKLOG.replace("### B0.1. First Task", "### B0.1 First Task")
        problems, _, _ = self.run_check(bad_backlog)
        self.assertTrue(any("malformed entry heading `### B0.1 First Task`" in p for p in problems), problems)

        bad_level = VALID_BACKLOG.replace("### B0.1. First Task", "## B0.1. First Task")
        problems_lvl, _, _ = self.run_check(bad_level)
        self.assertTrue(any("malformed entry heading `## B0.1. First Task`" in p for p in problems_lvl), problems_lvl)

        bad_completed = VALID_COMPLETED.replace("## 1. Initial Setup (B1.1)", "## §1. Initial Setup (B1.1)")
        problems_comp, _, _ = self.run_check(VALID_BACKLOG, completed_text=bad_completed)
        self.assertTrue(
            any("malformed milestone heading `## §1. Initial Setup (B1.1)`" in p for p in problems_comp),
            problems_comp,
        )

        bad_completed_h3 = VALID_COMPLETED.replace("## 1. Initial Setup (B1.1)", "### 1. Initial Setup (B1.1)")
        problems_h3, _, _ = self.run_check(VALID_BACKLOG, completed_text=bad_completed_h3)
        self.assertTrue(
            any("malformed milestone heading `### 1. Initial Setup (B1.1)`" in p for p in problems_h3),
            problems_h3,
        )

    def test_status_glyph_order_and_body_mentions_status(self):
        # Earlier glyph in the line wins over tuple order, and mentioning `**Status:**` in prose is ignored
        backlog = VALID_BACKLOG.replace(
            "* **Status:** 🚧 **In Progress** (formerly B2.3)",
            "* **Status:** 🚧 **In Progress** (was 📋 Planned; formerly B2.3)\n"
            "* **Automated Verification:** checks `**Status:**` line and **Completed** handling.",
        )
        problems, _, _ = self.run_check(backlog)
        self.assertEqual(problems, [])

    def test_formerly_case_insensitivity_and_multi_id_and_inplace_split(self):
        backlog = VALID_BACKLOG.replace(
            "(formerly B2.3)",
            "(formerly `b2.3`, B3.4 and B3.5)",
        )
        problems, _, (next_ids, _) = self.run_check(backlog)
        self.assertEqual(problems, [])
        self.assertEqual(next_ids, ["B0.2", "B1.3", "B2.4", "B3.6"])

        # Splitting B1.2 in place into B1.2a (formerly B1.2) should not reject B1.2a
        split_in_place = (
            VALID_BACKLOG.replace("**B1.2**", "**B1.2a**")
            .replace("#b12-second-task", "#b12a-second-task")
            .replace("### B1.2. Second Task", "### B1.2a. Second Task")
            .replace("(formerly B2.3)", "(formerly B1.2)")
        )
        problems_split, _, _ = self.run_check(split_in_place)
        self.assertEqual(problems_split, [])

        # Re-tiering one chunk while leaving a sibling chunk behind is rejected
        split_across_tiers = VALID_BACKLOG.replace(
            "| **B1.2** | [Second Task](#b12-second-task) | Core | 🚧 In Progress | **Frontier** | `src/` |",
            "| **B0.2a** | [Chunk A](#b02a-chunk-a) | Core | 📋 Planned | **Workhorse** | `src/` |\n"
            "| **B1.2b** | [Second Task](#b12b-second-task) | Core | 🚧 In Progress | **Frontier** | `src/` |",
        ).replace(
            "## Tier 1: High Priority",
            "### B0.2a. Chunk A\n* **Status:** 📋 **Planned** (formerly B1.2a)\n\n## Tier 1: High Priority",
        ).replace(
            "### B1.2. Second Task",
            "### B1.2b. Second Task",
        )
        problems_sibling, _, _ = self.run_check(split_across_tiers)
        self.assertTrue(
            any("sibling chunk B1.2a was retired" in p for p in problems_sibling),
            problems_sibling,
        )

    def test_brackets_in_table_title_and_inline_code_comments_and_fences(self):
        backlog = (
            VALID_BACKLOG.replace(
                "[First Task](#b01-first-task)",
                "[First `arr[0]` Task](#b01-first-arr0-task)",
            )
            .replace(
                "### B0.1. First Task",
                "### B0.1. First `arr[0]` Task",
            )
            .replace(
                "* **Why it matters:** test.",
                "* **Why it matters:** handles `<!--` in inline code and `[spike](spikes/missing.md)`.\n"
                "```python tools/check_backlog.py```",
                1,
            )
        )
        problems, _, _ = self.run_check(backlog)
        self.assertEqual(problems, [])

    def test_inplace_split_completed_chunk_and_umbrella_closure(self):
        # B1.2a (formerly B1.2) is in COMPLETED.md while B1.2b remains in BACKLOG.md
        backlog_remaining_b = (
            VALID_BACKLOG.replace("**B1.2**", "**B1.2b**")
            .replace("#b12-second-task", "#b12b-second-task")
            .replace("### B1.2. Second Task", "### B1.2b. Second Task")
            .replace(" (formerly B2.3)", "")
        )
        completed_with_chunk_a = """\
# Completed

## Index of Delivered Milestones

* **Chunk A ([§1](#1-chunk-a-b12a)):** first chunk done.

## 1. Chunk A (B1.2a)

* **Status:** ✅ **Completed (2026-10-01)** (formerly B1.2)
"""
        problems, _, _ = self.run_check(backlog_remaining_b, completed_text=completed_with_chunk_a)
        self.assertEqual(problems, [])

        # Once B1.2b also lands, an umbrella closure for B1.2 is valid
        backlog_only_b0 = """\
# Development Backlog

## Active Engineering Backlog

| ID | Task Name | Focus Area | Status | Model Fit | Target Surface / Script |
|:---:|---|---|:---:|:---:|---|
| **B0.1** | [First Task](#b01-first-task) | Hygiene | 📋 Planned | **Workhorse** | `tools/` |

## Tier 0: Repository Hygiene & Blockers

### B0.1. First Task
* **Status:** 📋 **Planned**
* **Why it matters:** test.
"""
        completed_with_umbrella = """\
# Completed

## Index of Delivered Milestones

* **Chunk A ([§1](#1-chunk-a-b12a)):** first chunk done.
* **Chunk B ([§2](#2-chunk-b-b12b)):** second chunk done.
* **Umbrella ([§3](#3-umbrella-b12)):** all chunks done.

## 1. Chunk A (B1.2a)

* **Status:** ✅ **Completed (2026-10-01)** (formerly B1.2)

## 2. Chunk B (B1.2b)

* **Status:** ✅ **Completed (2026-10-02)**

## 3. Umbrella (B1.2)

* **Status:** ✅ **Completed (2026-10-02)** · all chunks (B1.2a–B1.2b) delivered.
"""
        problems_umb, _, _ = self.run_check(backlog_only_b0, completed_text=completed_with_umbrella)
        self.assertEqual(problems_umb, [])

    def test_completed_heading_with_formerly_clause(self):
        completed_same_parens = """\
# Completed

## Index of Delivered Milestones

* **Initial Setup ([§1](#1-initial-setup-b11-formerly-b21)):** done.

## 1. Initial Setup (B1.1, formerly B2.1)
"""
        problems, _, (next_ids, _) = self.run_check(VALID_BACKLOG, completed_text=completed_same_parens)
        self.assertEqual(problems, [])
        self.assertEqual(next_ids, ["B0.2", "B1.3", "B2.4", "B3.1"])

        completed_two_parens = """\
# Completed

## Index of Delivered Milestones

* **Initial Setup ([§1](#1-initial-setup-b11-formerly-b25)):** done.

## 1. Initial Setup (B1.1) (formerly B2.5)
"""
        problems_two, _, (next_ids_two, _) = self.run_check(VALID_BACKLOG, completed_text=completed_two_parens)
        self.assertEqual(problems_two, [])
        self.assertEqual(next_ids_two, ["B0.2", "B1.3", "B2.6", "B3.1"])

        completed_only_formerly = """\
# Completed

## Index of Delivered Milestones

* **Initial Setup ([§1](#1-initial-setup-formerly-b25)):** done.

## 1. Initial Setup (formerly B2.5)
"""
        problems_missing_id, _, _ = self.run_check(VALID_BACKLOG, completed_text=completed_only_formerly)
        self.assertTrue(
            any("heading has no `(BN.x)` backlog ID at the end" in p for p in problems_missing_id),
            problems_missing_id,
        )

    def test_formerly_oxford_comma_inline_code_and_multiple_retired_chunks(self):
        # Oxford comma extracts all three IDs; inline code `(formerly B9.9)` is ignored
        backlog = VALID_BACKLOG.replace(
            "(formerly B2.3)",
            "(formerly B2.3, B3.4, and B3.5)\n"
            "* **Background:** keep old ID like `(formerly B9.9)` in status.",
        )
        problems, _, (next_ids, _) = self.run_check(backlog)
        self.assertEqual(problems, [])
        self.assertEqual(next_ids, ["B0.2", "B1.3", "B2.4", "B3.6"])

        # Multiple retired chunks of B1.2: B1.2a retired in-place by B1.2b, and B1.2c re-tiered to B0.2c
        multi_retired = VALID_BACKLOG.replace(
            "| **B1.2** | [Second Task](#b12-second-task) | Core | 🚧 In Progress | **Frontier** | `src/` |",
            "| **B0.2c** | [Chunk C](#b02c-chunk-c) | Core | 📋 Planned | **Workhorse** | `src/` |\n"
            "| **B1.2b** | [Second Task](#b12b-second-task) | Core | 🚧 In Progress | **Frontier** | `src/` |",
        ).replace(
            "## Tier 1: High Priority",
            "### B0.2c. Chunk C\n* **Status:** 📋 **Planned** (formerly B1.2c)\n\n## Tier 1: High Priority",
        ).replace(
            "### B1.2. Second Task",
            "### B1.2b. Second Task",
        )
        problems_multi, _, _ = self.run_check(multi_retired)
        self.assertTrue(
            any("sibling chunk B1.2c was retired" in p for p in problems_multi),
            problems_multi,
        )

    def test_multi_backtick_inline_code_and_fenced_body_line_cap(self):
        backlog = VALID_BACKLOG.replace(
            "* **Why it matters:** test.",
            "* **Why it matters:** ``code with `backtick` inside`` and `[spike](spikes/missing.md)`.",
            1,
        )
        problems, _, _ = self.run_check(backlog)
        self.assertEqual(problems, [])

        with_long_fence = VALID_BACKLOG + "```python\n" + "x = 1\n" * 10 + "```\n"
        problems_cap, _, _ = self.run_check(with_long_fence, max_lines=5)
        self.assertTrue(
            any("B1.2 (line 18): body is 14 non-blank lines (cap 5)" in p for p in problems_cap),
            problems_cap,
        )

    def test_status_note_separators_and_multiple_index_links_per_line(self):
        backlog = VALID_BACKLOG.replace(
            "* **Status:** 📋 **Planned**",
            "* **Status:** 📋 **Planned** — follow-up to completed spike",
        )
        completed = """\
# Completed

## Index of Delivered Milestones

* **Milestones ([§1](#1-first-b11), [§2](#2-second-b13)):** two on one line.

## 1. First (B1.1)

## 2. Second (B1.3)
"""
        problems, _, _ = self.run_check(backlog, completed_text=completed)
        self.assertEqual(problems, [])

    def test_subheadings_and_prose_formerly_allowed_in_body(self):
        backlog = VALID_BACKLOG.replace(
            "* **Why it matters:** test.",
            "* **Why it matters:** replaces old client (formerly B0.1 in early prototype).\n"
            "#### Edge Cases\n"
            "* Handles retries cleanly.",
            1,
        )
        problems, _, _ = self.run_check(backlog)
        self.assertEqual(problems, [])

    def test_repeated_number_is_reported_once_not_cascaded(self):
        completed = """\
# Completed

## Index of Delivered Milestones

* **A ([§1](#1-a-b11)):** a.
* **B ([§2](#2-b-b03)):** b.
* **C ([§2](#2-c-b04)):** c.
* **D ([§3](#3-d-b05)):** d.

## 1. A (B1.1)

## 2. B (B0.3)

## 2. C (B0.4)

## 3. D (B0.5)
"""
        problems, _, (_, next_sec) = self.run_check(VALID_BACKLOG, completed_text=completed)
        sequence = [p for p in problems if "expected milestone number" in p]
        self.assertEqual(len(sequence), 1, problems)  # the repeat; the 3 after it follows the second 2
        self.assertEqual(next_sec, 4)

    def test_legacy_marker_grandfathers_old_milestones_only(self):
        completed = """\
# Completed

<!-- backlog-check: legacy-through=3 -->

## Index of Delivered Milestones

* **D ([§4](#4-d-b07)):** d.

## 1. Pre-ID Milestone

## 3. Skipped a Number (B0.5)

## 3. Repeated a Number (B0.5)

## 4. D (B0.7)

## 4. Collides After the Cutoff (B0.8)
"""
        problems, _, (next_ids, next_sec) = self.run_check(VALID_BACKLOG, completed_text=completed)
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("§4" in p and "expected milestone number §5" in p for p in problems), problems)
        self.assertTrue(any("index for §4" in p for p in problems), problems)
        # grandfathered milestones still retire their IDs
        self.assertEqual(next_ids[0], "B0.9")
        self.assertEqual(next_sec, 5)

    def test_legacy_milestone_still_closes_its_id_for_the_backlog(self):
        completed = """\
# Completed

<!-- backlog-check: legacy-through=1 -->

## Index of Delivered Milestones

## 1. Old Work (B0.1)
"""
        problems, _, _ = self.run_check(VALID_BACKLOG, completed_text=completed)
        self.assertTrue(any("B0.1 (line" in p and "already closed" in p for p in problems), problems)

    def test_new_milestone_reclosing_a_legacy_id_is_flagged(self):
        completed = """\
# Completed

<!-- backlog-check: legacy-through=1 -->

## Index of Delivered Milestones

* **Again ([§2](#2-again-b15)):** again.

## 1. Old Work (B1.5)

## 2. Again (B1.5)
"""
        problems, _, _ = self.run_check(VALID_BACKLOG, completed_text=completed)
        self.assertTrue(any("B1.5 is closed in both §1 and §2" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
