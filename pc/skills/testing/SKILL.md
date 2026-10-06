---
name: testing
description: How to write tests that catch bugs and prove the work — edge cases, error branches, mocks of external dependencies, the arrange-act-assert shape, regression tests for caught bugs, coverage as a map of the holes. For writing tests, raising coverage and TDD.
---

# Skill: writing tests that prove something

A test exists to catch a breakage and to show that a feature works. A test that always passes and
checks nothing of substance is harmful ballast: it gives a false sense of safety.

## What to test first

* **Behaviour, not implementation.** Check the result and the observable effects, not the
  internals. Otherwise the test breaks on any refactoring while the code is right.
* **Edge cases** — that is where bugs live: empty (`[]`, `""`, `None`, 0), a single element, the
  maximum/minimum, negatives, duplicates, unicode, very large, invalid input.
* **Error branches.** Every `raise`, `try/except` and argument check is a path of its own. Check
  that bad input raises the right exception with a clear message, rather than "just crashing".
* **A regression test for a caught bug.** Fixed a bug — write a test at once that WOULD fail
  without the fix. That guarantees it does not come back.

## Shape

Arrange → Act → Assert: prepare the input, call one behaviour, check one result. One test — one
idea; the name says what exactly is checked
(`test_expand_without_placeholder_appends_argument`, not `test_1`).

## Mocks — only the boundaries, not your own code

Replace what is EXTERNAL and slow or non-deterministic: the network, LLM calls, time, randomness,
the file system, other people's services. Do not mock your own logic — then you test the mock,
not the code. In this project there is `ScriptedLLM` for the LLM, `EventCollector` for events,
and the `settings`/`tmp_path` fixtures for the working folder.

## Coverage is a map of the holes, not a goal

`test_coverage source=... tests=...` shows the uncovered lines. Use them as the list of what is
not checked yet (usually exactly the error branches and edge cases). But 100% for the number's
sake is not needed: a covered line is not a checked behaviour. The goal is a meaningful test for
the important paths and the edge cases.

## Order

1. Wrote or changed code — a test for the new behaviour at once (not "later").
2. `run_tests` — green. Red — fix the cause, do not bend the test to the bug.
3. Unsure whether everything is covered — `test_coverage`, then add the uncovered branches.
4. A test must fail if the code breaks: check in your head that it can catch an error at all. An
   always-green test — throw it away or fix it.
