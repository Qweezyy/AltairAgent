# Altair agent lab (2026-10)

The stand behind three articles: «Одна строчка в запросе…», «15% задач агент объявил выполненными…»,
«Три оптимизации токенов…». About 560 runs of the real Altair agent and ~900 single requests on
`glm-5.3-flash` through GateYourWay and OpenRouter.

## What is here

- `lab/proxy.py` — an OpenAI-compatible proxy between the agent and the provider. Logs every request
  (messages, tools, sizes, provider-reported prompt/cached/completion/reasoning tokens, time, cost),
  can change requests on the fly (`transforms.py`: drop prompt sections, cut tool descriptions,
  set a reasoning level, compact line numbers) and falls back to the second provider.
- `lab/stand.py` — runs a task: a fresh app folder and workspace, the backend, `altair -p`, then the
  task's check. `python stand.py CONFIG REPS TASK[,TASK]`.
- `lab/tasks.py`, `lab/hardtasks.py`, `lab/longtasks.py` — tasks with checks; the hard ones use
  hidden tests the agent never sees. `validate_hard.py` / `validate_long.py` check the hidden tests
  against reference solutions.
- `lab/exp*.py`, `lab/gw_*.py` — standalone experiments: cache behaviour, reasoning on service calls,
  masking of old outputs, exact token weights, history summaries, provider reliability and the 6-hour
  soak test.
- `lab/facts.py` — recomputes every number the articles use from `data/` into `data/facts.json`.
- `lab/charts.py` — draws the article charts (SVG + Playwright).
- `data/` — raw results: one JSON line per run (`results*/`, `r_*/`), the soak and probe logs.

## Running it

The scripts find the agent's code in `pc/` of this repository by themselves (they import it), so run
them from this folder with the PC agent's dependencies installed (`pip install -r pc/requirements.txt`):

```bash
python lab/facts.py                       # recompute every article number from data/ — no keys needed
python lab/stand.py base 3 h_logs,h_cache  # new runs: needs providers (below) and the agent's backend
```

Providers are read at run time from a local text file, `providers.txt` here or the path in
`ALTAIR_LAB_PROVIDERS` — one block per provider, separated by a blank line:

```
Name - GateYourWay, URL - https://api.example.com/v1, Key - <your key>, Model - glm-5.3-flash
```

The first block is the primary, the next ones are fallbacks. Never commit that file (it is ignored);
nothing secret is stored here. In `data/`, the per-run working folders are replaced by `<tmp>` and
whether a run hit the loop guard is kept as `_looped` (it was read from each run's backend log).
New results land in `results*/` next to the scripts.
