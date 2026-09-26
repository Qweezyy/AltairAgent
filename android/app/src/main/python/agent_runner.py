"""
Раннер пользовательского Python-кода для встроенного интерпретатора (Chaquopy).

Вызывается из Kotlin: agent_runner.run(code, workdir).
- Выполняет код в рабочей папке чата (песочница).
- Перехватывает stdout/stderr.
- Если установлен matplotlib — сохраняет открытые фигуры в PNG (backend Agg) и
  возвращает их пути (телефон покажет как картинки).
Возвращает JSON-строку: {"ok": bool, "output": str, "images": [paths]}.
"""

import io
import json
import os
import sys
import traceback

# Максимум символов вывода, чтобы не раздувать контекст модели.
_MAX_OUTPUT = 20000


def run(code, workdir):
    result = {"ok": True, "output": "", "images": []}
    buf = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = buf
    sys.stderr = buf

    # matplotlib без GUI (если вообще установлен) — рисуем в файлы.
    try:
        import matplotlib
        matplotlib.use("Agg")
    except Exception:
        pass

    try:
        if workdir:
            try:
                os.makedirs(workdir, exist_ok=True)
                os.chdir(workdir)
            except Exception:
                pass
        globals_env = {"__name__": "__main__"}
        exec(code, globals_env)

        # Сохраняем открытые фигуры matplotlib, если он есть.
        try:
            import matplotlib.pyplot as plt
            for i, num in enumerate(plt.get_fignums()):
                path = os.path.join(workdir or ".", "plot_%d.png" % i)
                plt.figure(num).savefig(path, bbox_inches="tight", dpi=140)
                result["images"].append(os.path.abspath(path))
            plt.close("all")
        except Exception:
            pass
    except Exception:
        result["ok"] = False
        traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = old_out, old_err

    out = buf.getvalue()
    if len(out) > _MAX_OUTPUT:
        out = out[:_MAX_OUTPUT] + "\n… (вывод обрезан)"
    result["output"] = out
    return json.dumps(result)
