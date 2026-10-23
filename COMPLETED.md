# Completed Milestones & Engineering Record

This document is the permanent historical ledger of completed milestones for this project. For
active and upcoming development tasks, see [`BACKLOG.md`](BACKLOG.md).

Entries are numbered `§1, §2, …` chronologically in the order they land and are never renumbered.
Each heading carries the backlog ID it closed, so the ID stays retired. Run `python tools/check_backlog.py`
to see the next `§N` number, append the new entry to the bottom of the file, append its `§N` line
to the bottom of `## Index of Delivered Milestones`, then delete the item from `BACKLOG.md` in the
same change.

---

## Index of Delivered Milestones

<!--
Index line template (append to the bottom of this section):

* **Short Title of What Shipped ([§1](#1-short-title-of-what-shipped-t01)):** one-line summary.
-->

---

<!--
Entry template (append below the last milestone entry at the bottom of the file, then add a line
to the index above):

## 1. Short Title of What Shipped (T0.1)

* **Status:** ✅ **Completed (YYYY-MM-DD)** · **Priority:** Tier 0 / Focus Area
* **Model Fit:** <tier or model that did the work>
* **Why it mattered:** the problem as it stood, in a sentence or two.
* **What changed:** the delivered behavior, files and interfaces touched, and anything removed.
* **Kept / deferred:** what was deliberately left alone, and any follow-up IDs it spawned.
* **Verified:** the tests or checks run, and any failures that were pre-existing.

Umbrella closure, added when the last lettered chunk of a parent ID lands (each chunk already
has its own entry):

## 7. Shared HTTP Client — Umbrella Closure (T1.1)

* **Status:** ✅ **Completed (YYYY-MM-DD)** · all chunks (T1.1a–T1.1b) delivered.
* **Why it mattered:** the problem the whole set solved, in a sentence or two.
* **Chunk → write-up map:**

  | Chunk | Title | Write-up |
  |:---:|---|:---:|
  | T1.1a | Give the Shared HTTP Client Timeouts and Retries | [§5](#5-...) |
  | T1.1b | Route Every Outbound HTTP Call Through the Shared Client | [§6](#6-...) |

* **Leftovers:** anything the chunks didn't finish, carried into a new backlog ID.
-->
