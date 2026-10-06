---
name: feature_delivery
description: How to bring a feature to done as one coherent increment — from scouting and a plan to tests, a live check, reviewing your own diff and an honest report. For adding a capability, an improvement or a noticeable change in a project.
---

# Skill: delivering a feature like an engineer

One feature is one coherent increment, finished and checked. Do not grab five things at once or
drop one halfway. The order below is the working loop, not a formality.

## 1. Understand and scout (before a single edit)

* Reread the task carefully. An ambiguity where a mistake is costly — clarify with `ask` (options
  in one click), but do not ask what you can check yourself.
* **Look for what exists before writing.** `grep_search`, `code_map`, `find_symbol` — half of
  "new" functionality is already somewhere. Duplicating it breeds bugs.
* Understand where to fit in: extension points, conventions, the code next door. New code must
  read as part of the project, not as a patch.

## 2. Plan (for anything non-trivial)

Several files or a new feature — `write_plan`: goal, approach, files touched, steps, how it will
be checked, risks. The user catches a wrong direction on the plan, not in finished code.

## 3. Build the increment

* Edits with `edit_file` (one place) or `apply_patch` (several places or files, atomically). Keep
  the change focused: do not mix an unrelated refactoring into the feature.
* Write in the style of the code next to it: the same names, indentation, comment density.

## 4. Check — always, before the word "done"

* `run_tests` + `run_lint` + `type_check` (Python). Red — read the report, fix the cause, repeat
  (up to 3–4 rounds). It does not work out — say honestly what fails.
* **Write a test for the new behaviour**, not only rerun the old ones: a test is the proof that the
  feature works and will not break later.
* **Check it for real, not only with a unit test:**
  - logic or a script — run it (`run_python`, `execute_command`);
  - a web interface — `screenshot_ui` + `audit_ui` (through the model's eyes, not by the code);
  - a live server or build — `start_dev_server` → `read_dev_server`.
* Reread your diff with `git_diff`: no debugging left, no accidental edits, no commented-out
  leftovers.

## 5. Record it and report

* Update the documentation if the behaviour changed.
* A meaningful commit with `git_commit` (in a git repository, when it fits).
* The summary in words: what was done, what was checked and HOW (not "done" but "ran the tests —
  green, took a screenshot — the layout is clean"), what is left. Do not claim what you did not
  check.

## What not to do

* Do not call unchecked work done.
* Do not pile unrelated changes together.
* Do not keep quiet about what you postponed or cut: say plainly what and why.
