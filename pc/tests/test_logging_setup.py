"""The log file keeps receiving lines when Windows refuses to rotate it."""

from __future__ import annotations

import logging

from core.logging_setup import FORMAT, _SharedRotatingFileHandler


def test_a_failed_rotation_does_not_silence_the_log(tmp_path, monkeypatch):
    path = tmp_path / "agent.log"
    handler = _SharedRotatingFileHandler(path, maxBytes=2_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    log = logging.getLogger("test.rotation")
    log.propagate = False
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    def locked(src, dst):  # what Windows says while another process has the file open
        raise PermissionError(32, "The process cannot access the file", str(src))

    monkeypatch.setattr(handler, "rotate", locked)
    try:
        for i in range(200):
            log.info("line %d after the log is full", i)
    finally:
        log.removeHandler(handler)
        handler.close()
    text = path.read_text(encoding="utf-8")
    assert "line 0 " in text and "line 199 " in text  # nothing was dropped
    assert text.count("after the log is full") == 200


def test_rotation_still_works_when_it_can(tmp_path):
    path = tmp_path / "agent.log"
    handler = _SharedRotatingFileHandler(path, maxBytes=2_000, backupCount=2, encoding="utf-8")
    log = logging.getLogger("test.rotation.ok")
    log.propagate = False
    log.addHandler(handler)
    try:
        for i in range(200):
            log.warning("line %d of a log that is long enough to need rotating", i)
    finally:
        log.removeHandler(handler)
        handler.close()
    assert (tmp_path / "agent.log.1").exists()
    assert "line 199" in path.read_text(encoding="utf-8")
