---
name: coding
description: How to program with this agent — code navigation, safe edits, the test → fix loop, rollback. For development, refactoring and repairs in any project and any language.
---

# Skill: writing code

This is about HOW to work with code through the agent's tools, not about the syntax of a language
(Python has its own skill, `python_expert`).

## Start a non-trivial task with a plan

Before changing code in a non-trivial task (several files, a new feature, a refactoring), call
`write_plan`: it saves `implementation_plan.md` (goal, approach, files touched, steps, how it will
be checked, risks) and shows it to the user for changes BEFORE the work starts. That is cheaper
than redoing the work: a wrong direction is caught on the plan, not in finished code. The plan's
steps become a live checklist — keep their statuses current as you go.

## The order in an unfamiliar project

1. **Map first, then read.** `code_map` gives a file's structure with line numbers — many times
   cheaper than reading whole files. Navigate by it. For JS/TS/Go/Rust/Java/C# the map is exact
   (tree-sitter): classes with methods, interfaces, traits, impl blocks, types, enums — as good
   as for Python.
2. **Before changing a function — `find_symbol`:** where it is defined and who calls it. A changed
   signature without checking the callers breaks code silently.
3. **`read_file` precisely** — the lines the map points to, not "read everything".

### Structural questions — `ast_search` (Python)

When you need to find something by STRUCTURE rather than by name, use `ast_search`: it parses the
syntax tree and is more precise than `grep_search`:
* `decorated_by` — functions/classes with a decorator (`@router.get`, `@app.websocket`);
* `subclass_of` — subclasses of a class (for example every `BaseModel`);
* `calls` — the real call sites of a function (attribute calls included);
* `raises` — where an exception is raised; `imports` — who imports a module;
* `async_functions`, `missing_return_type` — a walk by a property.

Example: before changing the contract of the endpoints — `ast_search decorated_by router.get`, to
see every affected handler at once.

### Semantics — `code_intel` (LSP)

When you need the precision of a real analyser rather than matching names — `code_intel`
(pyright for Python; tsserver/gopls/rust-analyzer when installed):
* `definition` — where a symbol leads (follows imports and re-exports);
* `references` — every use, semantically (tells same-named symbols apart);
* `hover` — the full signature and type of a symbol.

`find_symbol` is faster and enough for an overview; `code_intel` is for when a mistake matters
(same-named functions, chains of imports, the exact type).

## Edits

* One localised change in a file → `edit_file`: copy `old_text` exactly from the file (with its
  indentation) and make it unique with a few surrounding lines.
* Changes in several places or files, or creating/deleting/moving files in the same step →
  `apply_patch` (unified diff or the V4A format). It is atomic: every hunk applies or nothing
  changes, and only the changed lines go into the context.
* `write_file` — only for new files or a complete replacement.
* After Python edits — `type_check` (pyright): it catches type mismatches, undefined names and
  wrong arguments that `run_lint` (ruff) does not see and that would only show up at run time.
  Run it before the tests and before handing the work over.
* Write code that reads like the code next to it: the same style, indentation, naming and comment
  density. A new file must not stand out.
* Any edit can be undone with the "↩ Undo" button — do not count on it instead of care. A snapshot
  is taken automatically before every change.

## The checking loop (mandatory)

After code edits — **do not say "done" before checking**:

1. `run_tests` — the tests; `run_lint` — style; `type_check` — types.
2. Something failed — read the report, find the cause, fix it, run again. Repeat until green, but
   no more than 3–4 rounds in a row.
3. A few attempts did not do it — stop and say honestly what fails and what you tried. Do not pass
   off unchecked work as working.
4. The project has no tests — say so, and check the result by running the code (`run_python` /
   `execute_command`).
5. **Before handing over, reread your diff** — `git_diff` (in a git repository) with a reviewer's
   eye: no debugging leftovers, no accidental edits. See the `git` skill.

## Batch operations

Dozens of edits of the same kind (a rename, a replacement across many files) are faster as one
script via `run_python` than as many separate tool calls.

## What not to do

* Do not guess what a file contains — check with `grep_search` / `read_file`.
* Do not leave the code broken between steps without a clear reason.
* Do not claim what you did not do: a step failed — say so.
