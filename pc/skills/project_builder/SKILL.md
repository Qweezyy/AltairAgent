---
name: project_builder
description: Build a whole project in one go — an app, a site, a bot, a game, a script. First understand it and ask the clarifying questions up front, request the secrets it needs (API keys), make a detailed plan, then build and check everything autonomously, without asking again along the way. For big tasks like "make me an app/site that does X".
---

# Skill: building a project in one go

The user asks for something big as a whole ("make an app — a calorie counter"). The goal is a
finished, working result in one pass, without pulling them in with questions at every step. So
everything unclear is cleared up IN ADVANCE, and then the build runs on its own.

## 1. Understand and clarify — in rounds, not one long list

The goal is to clear up the essentials before building, but in a human way: questions come in
**rounds**, not as one questionnaire. The answers to the early questions often decide what is
worth asking next.

**How to clarify:**

* **Foundations first.** The first round is what everything else depends on: the platform/stack
  and the main goal. The user may not be technical — offer a sensible default and mark it
  `recommended`.
* **Group what is independent, separate what depends.** One `ask` call collects questions that do
  NOT affect each other (up to 6). If an answer changes the following questions, ask it on its own
  and wait for the answer.
  *Example:* "phone" vs "website" changes both the design questions and whether a backend is
  needed — so ask about the platform earlier and separately.
* **Narrow down by the answers.** Know the platform → ask what is specific to it (a phone: offline
  mode, an app store; a site: hosting, a domain).
* **Cover the directions** (as they become relevant, not all at once): features (required /
  optional), data (input / storage / external API), external keys, design (style, theme, the
  interface language).
* **Do not ask the obvious.** Where there is a sensible default, take it yourself and say so. A
  question asked out of politeness is noise.

The measure: ask only when the fork is real and a mistake is costly. Redoing finished work costs
more than one question — but ten questions where two would do are annoying too. Usually 2–4
rounds of a few questions make the picture clear.

## 2. Request the secrets (if any are needed)

An API key is needed — call `request_secret name=... purpose=...`. The user enters it in the
protected Secrets panel; you never see the value and refer to it by name (`os.environ['NAME']`).
Say the key can be entered **now or later** — do not block on it: build with a stub or a branch,
and test with the real key once it is there (`list_secrets` shows whether it is set).

## 3. Make a detailed plan

`write_plan` — goal, approach, every file, the order of steps, how it will be checked, risks. The
user sees it in the Preview and corrects it before the build. It is the contract for the whole
run.

## 4. Build autonomously — in one pass

* Follow the plan, keeping the checklist current (`update_plan`). Do NOT ask about small things —
  decide yourself within the agreed plan.
* Create files in batches; similar ones with a script (`run_python`), not one by one.
* Keep the project runnable: do not leave it broken between steps for long.

## 5. Check for real and hand over

* Tests, lint, types (see the `feature_delivery` skill), then **a live check**: web/UI —
  `start_dev_server` + `screenshot_ui`/`audit_ui`; a script — run it.
* If the key is already entered — run the scenario with the real API; if not — check everything
  that can be checked without it, and say explicitly what is left to test once the key is in.
* The summary: what was built, **how to run it** (step by step, for a non-programmer), what was
  checked and what is left (for example "enter the key and the generation will work").

## The principle

Clarifications — in rounds and with purpose (from the main things to the details), not a
questionnaire and not one question per message. Secrets — by name, safely. The plan — detailed
and agreed. The build — autonomous and checked. The user takes part at the start (clarifying)
and at the end (the finished result), not pulled in over small things at every step.
