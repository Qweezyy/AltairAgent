---
name: git
description: Working with git like a developer — checking the status and your own diff before handing over, reading history with log and blame, making meaningful atomic commits, keeping branches. For any task in a git repository.
---

# Skill: working with git

git is the project's memory. Through it you understand what is already done and why the code is
the way it is, and you record changes in portions that can be undone.

## Before working — look around

* `git_status` — what is changed, added, untracked right now. The first thing in an unfamiliar
  state: know where you start from.
* `git_log path=...` — how a file changed. Recent history explains the intent better than reading
  the code blind.
* `git_blame path=... start_line=.. end_line=..` — who brought these lines in and in which commit.
  Indispensable for "why is it like this and not otherwise": you find the original commit and its
  message.

## Check YOUR diff before calling it done

After edits — `git_diff` (no arguments: every change in the working tree). Read it as a reviewer:
no debugging leftovers, no accidental edits, nothing removed by mistake. It is the same as
rereading a letter before sending it. `git_diff against=HEAD~1` — compare with the previous
commit; `staged=true` — only what is already staged.

## Commit meaningfully

* `git_commit message="..."` records the changes. By default it takes every tracked change;
  `paths=[...]` — only the files you name.
* One commit = one logical change. Do not pile unrelated edits together: later they cannot be
  undone separately.
* The message — short and to the point: WHAT and WHY, not "fix" or "edits".
* Commit only when asked, or when a logical piece is done and checked (tests and lint green). Do
  not commit a broken state.

## Branches for big tasks

`git_branch action=create name=...` creates a branch and switches to it. Keep a big feature or a
risky refactoring in its own branch, so it does not get in the way of the main one.
`action=list` — see the branches, `action=switch` — switch.

## Limits

* **Pushing is not done by tools.** Sending to a remote (`git push`) is the user's action; offer
  it, do not do it yourself.
* Do not rewrite published history (`reset --hard`, `rebase` of others' commits, `push --force`)
  without an explicit request — it breaks other people's work.
* If the folder is not a git repository, `git_status` suggests `git init` — but start a repository
  only with the user's consent.
