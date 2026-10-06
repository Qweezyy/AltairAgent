"""Bodies' keys: a body lets in only the bodies it trusts, by a signed challenge, and a revoked
body is out at once.

Checked end to end through the app (the test client posing as a machine on the network): no
token — no entry; a trusted body signs the challenge and gets a session token that opens the
API; a replayed challenge, a signature made for another server, an unknown body and a card that
claims someone else's id are all refused; revoking ends the sessions.
"""

from __future__ import annotations

import base64
import os

import pytest
from fastapi.testclient import TestClient

from core.bodies import Identity, TrustStore, body_id, signed_message


@pytest.fixture()
def remote(monkeypatch, settings):
    """The app, reached as from another machine (not loopback)."""
    import core.settings as settings_module
    import server.app as app_module
    import server.remote_auth as remote_auth
    import server.ws as ws_module
    from server.app import create_app

    for mod in (settings_module, app_module, ws_module):
        monkeypatch.setattr(mod, "get_settings", lambda: settings)
    monkeypatch.setattr(remote_auth, "get_settings", lambda: settings)
    monkeypatch.setattr(remote_auth, "LOOPBACK_HOSTS", frozenset({"127.0.0.1"}))
    monkeypatch.setattr(app_module, "_LOOPBACK_HOSTS", {"127.0.0.1"})
    settings.bridge_token = ""
    with TestClient(create_app()) as tc:
        yield tc


def _sign_in(tc: TestClient, body: Identity) -> tuple[int, str]:
    challenge = tc.post("/api/bodies/challenge", json={"id": body.id}).json()
    r = tc.post("/api/bodies/login", json={"id": body.id, "nonce": challenge["nonce"],
                                           "signature": body.answer(challenge["nonce"], challenge["verifier"])})
    return r.status_code, (r.json().get("token") if r.status_code == 200 else "")


def test_a_trusted_body_signs_in_and_an_unknown_one_does_not(remote, settings, tmp_path):
    assert remote.get("/api/sessions").status_code == 401           # nothing open without proof
    server_trust = TrustStore(settings.data_dir / "identity")
    laptop = Identity(tmp_path / "laptop", "pc", "laptop")
    stranger = Identity(tmp_path / "stranger", "pc", "stranger")
    server_trust.add(laptop.card())

    code, token = _sign_in(remote, laptop)
    assert code == 200 and token
    assert remote.get("/api/sessions", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert _sign_in(remote, stranger)[0] == 401


def test_a_challenge_is_answered_once_and_only_for_this_server(remote, settings, tmp_path):
    laptop = Identity(tmp_path / "laptop")
    TrustStore(settings.data_dir / "identity").add(laptop.card())
    ch = remote.post("/api/bodies/challenge", json={"id": laptop.id}).json()
    good = laptop.answer(ch["nonce"], ch["verifier"])
    # Signed for another server: refused (a signature cannot be carried to a second body).
    other = laptop.answer(ch["nonce"], "some-other-server")
    assert remote.post("/api/bodies/login", json={"id": laptop.id, "nonce": ch["nonce"], "signature": other}).status_code == 401
    # The challenge was used up by that attempt; even the right signature cannot replay it.
    assert remote.post("/api/bodies/login", json={"id": laptop.id, "nonce": ch["nonce"], "signature": good}).status_code == 401


def test_revoking_a_body_ends_its_sessions(remote, settings, tmp_path, monkeypatch):
    laptop = Identity(tmp_path / "laptop")
    TrustStore(settings.data_dir / "identity").add(laptop.card())
    _, token = _sign_in(remote, laptop)
    auth = {"Authorization": f"Bearer {token}"}
    assert remote.get("/api/sessions", headers=auth).status_code == 200
    # Revoking is the owner's call, from this PC (loopback) — the remote fixture is not.
    assert remote.post(f"/api/bodies/{laptop.id}/revoke", headers=auth).status_code == 403
    import server.remote_auth as remote_auth
    import server.app as app_module

    monkeypatch.setattr(remote_auth, "LOOPBACK_HOSTS", frozenset({"testclient"}))
    monkeypatch.setattr(app_module, "_LOOPBACK_HOSTS", {"testclient"})
    assert remote.post(f"/api/bodies/{laptop.id}/revoke").json() == {"ok": True, "sessions_ended": 1}
    monkeypatch.setattr(remote_auth, "LOOPBACK_HOSTS", frozenset({"127.0.0.1"}))
    assert remote.get("/api/sessions", headers=auth).status_code == 401
    assert _sign_in(remote, laptop)[0] == 401
    listed = TrustStore(settings.data_dir / "identity").get(laptop.id)
    assert listed is not None and listed.revoked                        # kept, marked, with the time


def test_a_card_cannot_claim_another_bodys_id(tmp_path):
    trust = TrustStore(tmp_path)
    real = Identity(tmp_path / "a")
    fake = dict(real.card(), id="someone-elses-id")
    with pytest.raises(ValueError):
        trust.add(fake)
    with pytest.raises(ValueError):
        trust.add({"public_key": base64.b64encode(b"short").decode()})
    assert trust.add(real.card()).id == real.id == body_id(real.public_key)


def test_the_key_is_made_once_and_kept_private(tmp_path):
    a = Identity(tmp_path)
    first = a.id
    b = Identity(tmp_path)
    assert b.id == first                                                  # the same body after a restart
    assert "PRIVATE KEY" in (tmp_path / "body_key.pem").read_text(encoding="ascii")
    if os.name != "nt":
        assert (tmp_path / "body_key.pem").stat().st_mode & 0o077 == 0
    sig = a.sign(signed_message("n", "v"))
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    Ed25519PublicKey.from_public_bytes(a.public_key).verify(base64.b64decode(sig), signed_message("n", "v"))


def test_the_bridge_token_still_works_for_the_phone(remote, settings):
    settings.bridge_token = "lan-secret"
    assert remote.get("/api/sessions", headers={"Authorization": "Bearer lan-secret"}).status_code == 200
    assert remote.get("/api/sessions", headers={"Authorization": "Bearer wrong"}).status_code == 401
