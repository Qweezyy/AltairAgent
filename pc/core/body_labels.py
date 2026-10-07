"""The owner's labels for bodies ("builds", "prod", "experiments"): what each one is for.

Kept on the PC by body id, for any body — this PC, a server, later the phone. The agent will read
them when it picks where to run something (stage 3 of 0.3.0).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from core.fs_atomic import atomic_write_text
from core.logging_setup import get_logger

logger = get_logger("body_labels")

MAX_LABEL = 32
_lock = threading.Lock()


def clean(labels: list[str]) -> list[str]:
    """Trimmed, without empties and repeats (case-insensitive), in the order given."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in labels:
        label = " ".join(str(raw).split())[:MAX_LABEL]
        if label and label.lower() not in seen:
            seen.add(label.lower())
            out.append(label)
    return out


class BodyLabels:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "bodies" / "labels.json"

    def all(self) -> dict[str, list[str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            logger.warning("body labels unreadable (%s): starting empty", exc)
            return {}
        return {str(k): clean(v) for k, v in data.items() if isinstance(v, list)}

    def get(self, body_id: str) -> list[str]:
        return self.all().get(body_id, [])

    def set(self, body_id: str, labels: list[str]) -> list[str]:
        with _lock:
            data = self.all()
            labels = clean(labels)
            if labels:
                data[body_id] = labels
            else:
                data.pop(body_id, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps(data, ensure_ascii=False, indent=1))
        return labels
