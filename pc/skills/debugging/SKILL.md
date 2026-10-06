---
name: debugging
description: How to find the cause of a hard bug instead of treating the symptom — a minimal reproduction, testing hypotheses one at a time while changing one factor at a time, a binary search for the difference. For puzzling failures, "works here but not there", hangs and bugs whose cause is not visible at once.
---

# Skill: real debugging

A hard bug is fixed by understanding its cause, not by trying things. A symptom treated blindly
comes back and takes time with it. The method below was proven on real puzzling failures (a
pseudo-terminal that hung, a language server that answered empty).

## 1. Reproduce it minimally

First a reliable reproduction on as small a piece as possible. A separate script or test that
fails every time is worth gold: without it you are guessing. Reduce a large system to 10 lines
that repeat the problem.

## 2. Change ONE factor at a time

The key technique. There is "works" and "does not work" — find the smallest difference between
them, trying one thing at a time:

* works in one thread but not in another? → it is the thread;
* works from a script but not under the server? → it is the server's environment;
* works with a direct import but not through a re-export? → it is how paths are resolved.

Each step answers one yes/no question and halves the space of causes. Do not change two factors
at once — you will not know which one was to blame.

## 3. Instrument, do not imagine

Not "it probably hangs here" — put in markers: logs with times, counters, printed intermediate
values, process states. Let the system show what happens and when. The "obvious" cause often
turns out not to be it.

## 4. Test hypotheses, do not defend them

Made a guess — think of a cheap experiment that would DISPROVE it. Confirming is more pleasant,
but the one who tries to break their own version finds the truth faster. A rejected hypothesis is
progress, not failure.

## 5. Found the cause — keep the knowledge

The real cause is usually not obvious and is easily forgotten. Leave it in a comment by the code
("this way and not the other, because …") and, if it is a lasting trait of the platform or stack,
in a skill. Otherwise in a month someone (or you) walks the same hours-long path again.

## When to stop

If a few rounds do not bring you closer to the cause, do not keep hitting the same spot. Step
back, reread the task, change the angle, try the opposite hypothesis. And tell the user honestly
that you are stuck and what you have checked — that is not defeat, it is data.
