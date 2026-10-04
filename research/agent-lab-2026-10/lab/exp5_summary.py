"""Experiment 5: the history summary call (runner._summarize_history) under a reasoning model."""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pc"))
import ctx  # noqa: E402
from core.agent.runner import _format_for_summary  # noqa: E402
from prov import chat, usage, text_of  # noqa: E402

SYSTEM = "Ты сжимаешь начало диалога в краткую памятку для продолжения работы."
USER = ("Сожми это начало диалога агента с пользователем в короткое резюме "
        "(5–8 предложений): что просил пользователь, что сделал агент, какие "
        "факты, договорённости и решения важны для продолжения. Пиши по делу, "
        "без воды.\n\n")
transcript = _format_for_summary(ctx.history(6))
print("transcript chars", len(transcript))
for prov in (0, 1):
    for cap in (600, 2000):
        empty, outs, reas, secs = 0, [], [], []
        for _ in range(4):
            try:
                res = chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER + transcript}],
                           max_tokens=cap, only=prov, retries=0, timeout=120)
            except Exception as exc:  # noqa: BLE001
                print("   error", str(exc)[:100]); continue
            u = usage(res); t = text_of(res).strip()
            empty += not t
            outs.append(len(t)); reas.append(u["reasoning"]); secs.append(res["seconds"])
            fin = res["json"]["choices"][0].get("finish_reason")
        if outs:
            print(f"provider {prov} max_tokens={cap}: empty {empty}/{len(outs)}  summary chars avg {statistics.mean(outs):5.0f}"
                  f"  reasoning avg {statistics.mean(reas):5.0f}  {statistics.mean(secs):4.1f}s  last finish={fin}", flush=True)
