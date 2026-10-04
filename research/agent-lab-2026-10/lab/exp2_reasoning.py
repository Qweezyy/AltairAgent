"""Experiment 2: reasoning on small internal calls (chat titles) — cost, time, off switches."""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pc"))
from core.agent.titler import _PROMPT, clean_title  # noqa: E402
from prov import chat, usage, text_of  # noqa: E402

TASKS = [
    "Проверь логику скринов браузера, довольно часто пустые возвращаются",
    "Write a Python script that renames all photos in a folder by their EXIF date",
    "Почини главный экран и анимацию, сверху непонятная полоска появилась",
    "Find the three cheapest flights from Moscow to Istanbul next week and compare baggage rules",
    "Сделай левый рельс с чатами настраиваемым по ширине, как и правые вкладки",
    "Explain why my pytest suite is flaky on CI but passes locally",
]
MODES = {
    "default": None,
    "thinking.disabled (Z.ai)": {"thinking": {"type": "disabled"}},
    "reasoning.enabled=false (OpenRouter)": {"reasoning": {"enabled": False}},
    "reasoning.effort=low": {"reasoning": {"effort": "low"}},
    "reasoning_effort=none (OpenAI style)": {"reasoning_effort": "none"},
}

for prov in (0, 1):
    print(f"provider {prov}")
    for mode, extra in MODES.items():
        rs, secs, outs, good = [], [], [], 0
        for task in TASKS:
            try:
                res = chat([{"role": "user", "content": _PROMPT.format(task=task)}], max_tokens=400,
                           extra=extra, only=prov, timeout=60, retries=0)
            except Exception as exc:  # noqa: BLE001
                print(f"  {mode:38} error: {str(exc)[:120]}")
                break
            u = usage(res)
            rs.append(u["reasoning"]); secs.append(res["seconds"]); outs.append(u["out"])
            good += bool(clean_title(text_of(res)))
        else:
            print(f"  {mode:38} reasoning avg={statistics.mean(rs):6.0f} max={max(rs):4}  out avg={statistics.mean(outs):5.0f}"
                  f"  time avg={statistics.mean(secs):5.1f}s max={max(secs):5.1f}s  >15s: {sum(s > 15 for s in secs)}"
                  f"  titles ok {good}/{len(TASKS)}", flush=True)
