"""Charts for the three articles: plain SVG (the palette of the 58% article) screenshotted by
Playwright. Every number comes from data/facts.json (facts.py) or from the logged experiment
outputs quoted in the comments. Writes RU and EN versions:
  ../../article-1-reasoning/img, ../../article-2-false-done/img, ../../article-3-costly-savings/img
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

BG, FG, DIM, GRID, OLD, NEW, BAD = "#101014", "#e8e6e1", "#8a8780", "#26262c", "#5b5d66", "#f2b544", "#d9604c"
ROOT = Path(__file__).resolve().parents[2]
F = json.loads((Path(__file__).resolve().parent.parent / "data" / "facts.json").read_text(encoding="utf-8"))


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def hbars(title, sub, rows, unit, w=1200, label_w=330):
    """rows: (label, value, note, color)."""
    L, R, T = label_w, 230, 110
    rh = 62
    h = T + rh * len(rows) + 40
    vmax = max(r[1] for r in rows) * 1.08
    pw = w - L - R
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" font-family="Segoe UI, Arial">',
           f'<rect width="100%" height="100%" fill="{BG}"/>',
           f'<text x="40" y="44" fill="{FG}" font-size="25" font-weight="600">{esc(title)}</text>',
           f'<text x="40" y="76" fill="{DIM}" font-size="15">{esc(sub)}</text>']
    for i, (label, value, note, color) in enumerate(rows):
        y = T + i * rh
        bw = pw * value / vmax
        out += [f'<text x="{L-16}" y="{y+28}" fill="{FG}" font-size="16" text-anchor="end">{esc(label)}</text>',
                f'<rect x="{L}" y="{y+8}" width="{pw}" height="30" rx="4" fill="{GRID}"/>',
                f'<rect x="{L}" y="{y+8}" width="{max(bw, 3)}" height="30" rx="4" fill="{color}"/>',
                f'<text x="{L+max(bw, 3)+12}" y="{y+29}" fill="{FG}" font-size="16" font-weight="600">{esc(unit(value))}</text>',
                f'<text x="{w-24}" y="{y+29}" fill="{DIM}" font-size="15" text-anchor="end">{esc(note)}</text>']
    out.append("</svg>")
    return "\n".join(out)


def g(path, key):
    d = F
    for part in path:
        d = d[part]
    return d[key]


def m(x):
    return f"{x * 1000:.1f} m$"


def build(lang):
    ru = lang == "ru"
    T = (lambda r, e: r if ru else e)
    w2 = F["a1_reasoning_openrouter_wave2"]
    w3 = F["a1_adaptive_openrouter_wave3"]
    lng = F["a1_long_tasks"]
    gw0, gw1 = F["a1_gateyourway_before"], F["a1_gateyourway_after_shipping"]
    out = {}

    def solved(s):
        return T(f"решено {s['passed']}/{s['runs']} · {s['minutes_per_run']:.1f} мин",
                 f"solved {s['passed']}/{s['runs']} · {s['minutes_per_run']:.1f} min")

    # ---- article 1
    rows = [(T("без параметра (как было)", "no level (before)"), w2["base"]["usd_per_run"], solved(w2["base"]), BAD),
            ("effort = low", w2["eff_low"]["usd_per_run"], solved(w2["eff_low"]), NEW),
            ("effort = medium", w2["eff_medium"]["usd_per_run"], solved(w2["eff_medium"]), OLD),
            ("effort = high", w2["eff_high"]["usd_per_run"], solved(w2["eff_high"]), OLD)]
    out["a1-cost"] = hbars(T("Цена одной трудной задачи", "Cost of one hard task"),
                           T("glm-5.3-flash через OpenRouter, 6 задач со скрытыми тестами, цена по счёту провайдера",
                             "glm-5.3-flash via OpenRouter, 6 tasks with hidden tests, cost as billed by the provider"), rows, m)
    rows = [(T("без параметра", "no level"), w2["base"]["reasoning_tokens_per_run"], "", BAD),
            ("effort = high", w2["eff_high"]["reasoning_tokens_per_run"], "", OLD),
            ("effort = medium", w2["eff_medium"]["reasoning_tokens_per_run"], "", OLD),
            ("effort = low", w2["eff_low"]["reasoning_tokens_per_run"], "", NEW)]
    out["a1-reasoning"] = hbars(T("Токены рассуждений на задачу", "Reasoning tokens per task"),
                                T("Без параметра модель решает сама, сколько думать — иногда 33 тыс. токенов за один шаг",
                                  "Without a level the model decides — sometimes 33K tokens in a single step"),
                                rows, lambda v: f"{int(v):,}".replace(",", " "))
    rows = [("low", w3["eff_low"]["usd_per_run"], solved(w3["eff_low"]), OLD),
            ("high", w3["eff_high"]["usd_per_run"], solved(w3["eff_high"]), OLD),
            (T("high на первом шаге (план)", "high on the first step (plan)"), w3["plan_high"]["usd_per_run"], solved(w3["plan_high"]), OLD),
            (T("low, high после ошибки", "low, high after a failure"), w3["escalate"]["usd_per_run"], solved(w3["escalate"]), NEW)]
    out["a1-adaptive"] = hbars(T("Когда думать сильнее", "When to think harder"),
                               T("18 прогонов на вариант, те же 6 задач, OpenRouter", "18 runs per variant, the same 6 tasks, OpenRouter"),
                               rows, m)
    rows = [(T("без параметра", "no level"), lng["base"]["usd_per_run"], solved(lng["base"]), BAD),
            ("effort = low", lng["eff_low"]["usd_per_run"], solved(lng["eff_low"]), NEW),
            ("effort = medium", lng["eff_medium"]["usd_per_run"], solved(lng["eff_medium"]), NEW)]
    out["a1-long"] = hbars(T("Длинные задачи (15–20 шагов)", "Long tasks (15–20 steps)"),
                           T("6 багов в пакете, аудит 12 значений по 60 файлам, «прочитай 8 файлов и ответь»",
                             "6 bugs in a package, auditing 12 values across 60 files, read 8 files then answer"), rows, m)

    # ---- article 2
    a2 = F["a2_wave1"]
    ok = a2["runs"] - a2["false_done"]
    rows = [(T("задача решена", "task solved"), ok, T("проверено тестами", "checked by tests"), NEW),
            (T("«готово» = текст ошибки шлюза", "'done' = gateway error text"), a2["by_reason"]["gateway_text"], "HTTP 200", BAD),
            (T("«готово» = пустой поток", "'done' = empty stream"), a2["by_reason"]["empty_stream"], T("103 с тишины", "103 s of silence"), BAD),
            (T("модель не справилась", "the model failed"), max(a2["model_failures"], 0.0001), "0", OLD)]
    out["a2-outcomes"] = hbars(T("46 задач, которые агент назвал выполненными", "46 tasks the agent called done"),
                               T("Каждую проверяли тестами после. Все провалы — провайдер, не модель",
                                 "Each checked by tests afterwards. Every failure was the provider, not the model"),
                               rows, lambda v: str(int(round(v))))
    rv = F["a2_reviewer"]
    alone = rv["agent_alone_low"]
    rows = [(T("агент без проверяющего", "agent alone"), alone["usd_per_run"], solved(alone), NEW),
            (T("+ проверяющий (low)", "+ reviewer (low)"), rv["verify_low"]["usd_per_run"],
             T(f"{rv['verify_low']['passed_before']}→{rv['verify_low']['passed_after']} из {rv['verify_low']['runs']}, ложных {rv['verify_low']['false_alarms']} из {rv['verify_low']['flagged']}",
               f"{rv['verify_low']['passed_before']}→{rv['verify_low']['passed_after']} of {rv['verify_low']['runs']}, {rv['verify_low']['false_alarms']} of {rv['verify_low']['flagged']} flags false"), OLD),
            (T("+ проверяющий (high)", "+ reviewer (high)"), rv["verify_high"]["usd_per_run"],
             T(f"{rv['verify_high']['passed_before']}→{rv['verify_high']['passed_after']} из {rv['verify_high']['runs']}, ложных {rv['verify_high']['false_alarms']} из {rv['verify_high']['flagged']}",
               f"{rv['verify_high']['passed_before']}→{rv['verify_high']['passed_after']} of {rv['verify_high']['runs']}, {rv['verify_high']['false_alarms']} of {rv['verify_high']['flagged']} flags false"), BAD)]
    out["a2-reviewer"] = hbars(T("Второй проверяющий после «готово»", "A second reviewer after 'done'"),
                               T("Отдельный вызов модели сверяет код с задачей; при замечаниях агент дорабатывает",
                                 "A separate model call checks the code against the task; the agent fixes what it flags"), rows, m)

    # ---- article 3
    ab = F["a3_prompt_ablations_low"]

    def steps(s):
        return T(f"решено {s['passed']}/{s['runs']} · {s['steps_per_run']} шагов", f"solved {s['passed']}/{s['runs']} · {s['steps_per_run']} steps")

    rows = [(T("полный промпт", "full prompt"), ab["base+low"]["usd_per_run"], steps(ab["base+low"]), NEW),
            (T("урезанный промпт", "trimmed prompt"), ab["lean+low"]["usd_per_run"], steps(ab["lean+low"]), BAD),
            (T("короткие описания инструментов", "short tool descriptions"), ab["shorttools+low"]["usd_per_run"], steps(ab["shorttools+low"]), BAD)]
    out["a3-prompt"] = hbars(T("Экономия на промпте", "Saving on the prompt"),
                             T("18 прогонов на вариант, 6 трудных задач, effort=low", "18 runs per variant, 6 hard tasks, effort=low"), rows, m)
    rc = F["a3_recall_budget"]
    rows = [(T("обычный бюджет (120K)", "normal budget (120K)"), rc["base"]["usd_per_run"],
             T(f"кэш {rc['base']['cache_share']:.0%} · {rc['base']['steps_per_run']} шагов", f"cache {rc['base']['cache_share']:.0%} · {rc['base']['steps_per_run']} steps"), NEW),
            (T("сжатый бюджет (40K)", "tight budget (40K)"), rc["tight"]["usd_per_run"],
             T(f"кэш {rc['tight']['cache_share']:.0%} · {rc['tight']['steps_per_run']} шагов", f"cache {rc['tight']['cache_share']:.0%} · {rc['tight']['steps_per_run']} steps"), BAD)]
    out["a3-clearing"] = hbars(T("Агрессивная очистка контекста", "Aggressive context clearing"),
                               T("«Прочитай 8 файлов, потом ответь на 6 вопросов» — 3 прогона, 6/6 ответов в обоих",
                                 "'Read 8 files, then answer 6 questions' — 3 runs, 6/6 right in both"), rows, m)
    # exp1 (logged output): share of the ~25.7K-token prompt served from the provider's cache
    rows = [(T("история дописана в конец", "history appended"), 1.00, "25 664 / 25 737", NEW),
            (T("system-заметка в конце", "a system note at the end"), 1.00, "25 792 / 25 842", NEW),
            (T("старый вывод скрыт", "an old output masked"), 0.06, T("кэш только до правки", "cache up to the edit"), BAD),
            (T("добавлен 1 инструмент в конец", "one tool appended"), 0.0001, "0 / 25 820", BAD),
            (T("1 инструмент по алфавиту", "one tool, sorted in"), 0.06, "1 664 / 25 820", BAD)]
    out["a3-cache"] = hbars(T("Что ломает кэш провайдера", "What breaks the provider's cache"),
                            T("Доля запроса (~25,7K токенов) из кэша, GLM через GateYourWay", "Share of a ~25.7K-token request served from cache, GLM via GateYourWay"),
                            rows, lambda v: f"{v:.0%}")
    # exp4 (logged): the same 1 320 lines of real files, counted by the provider
    rows = [("    12 | text", 21521, T("+44% к тексту без номеров", "+44% over plain text"), BAD),
            ("    12<tab>text", 20346, "+36%", OLD),
            ("12|text", 18587, "+24%", NEW),
            (T("без номеров", "no numbers"), 14950, "", OLD)]
    out["a3-lines"] = hbars(T("Номера строк в выводе read_file", "Line numbers in read_file output"),
                            T("1 320 строк настоящих файлов, токены по счёту провайдера", "1,320 lines of real files, tokens as counted by the provider"),
                            rows, lambda v: f"{int(v):,}".replace(",", " "))
    return out


def shoot():
    targets = {"a1": "article-1-reasoning", "a2": "article-2-false-done", "a3": "article-3-costly-savings"}
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for lang in ("ru", "en"):
            for name, svg in build(lang).items():
                folder = ROOT / targets[name[:2]] / "img"
                folder.mkdir(parents=True, exist_ok=True)
                p = b.new_page(device_scale_factor=2)
                p.set_content(f'<html><body style="margin:0;background:{BG}">{svg}</body></html>')
                p.locator("svg").screenshot(path=str(folder / f"{name}{'' if lang == 'ru' else '-en'}.png"))
        b.close()


if __name__ == "__main__":
    shoot()
    print("ok")
