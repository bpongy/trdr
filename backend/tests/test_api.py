from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient

from trdr.api.auth import AuthError, AuthService, hash_password, verify_password
from trdr.api.main import create_app
from trdr.db import utcnow
from trdr.models import Position, Signal, SignalStatus

from .conftest import NOW

PASSWORD = "un mot de passe solide"
TOTP_SECRET = pyotp.random_base32()


@pytest.fixture
def auth_settings(settings):
    return settings.model_copy(
        update={
            "auth_password_hash": hash_password(PASSWORD, n=2**4),
            "auth_totp_secret": TOTP_SECRET,
            "jwt_secret": "s" * 48,
        }
    )


@pytest.fixture
def client(auth_settings, session_factory):
    return TestClient(create_app(auth_settings, session_factory))


@pytest.fixture
def headers(client):
    response = client.post("/api/auth/login", json={"password": PASSWORD, "code": pyotp.TOTP(TOTP_SECRET).now()})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def add_signal(session_factory, **overrides) -> int:
    fields = dict(
        symbol="ETH/USDT", timeframe="4h", candle_time=NOW, price=100.0, stop_loss=95.0,
        take_profit=110.0, rsi=41.0, sma_fast=105.0, sma_slow=98.0,
    )  # fmt: skip
    with session_factory() as session:
        sig = Signal(**(fields | overrides))
        session.add(sig)
        session.commit()
        return sig.id


def test_password_hash():
    stored = hash_password(PASSWORD, n=2**4)
    assert "$" not in stored
    assert verify_password(PASSWORD, stored)
    assert not verify_password("autre", stored)
    assert not verify_password(PASSWORD, "n'importe quoi")


def test_health_is_public_and_the_rest_is_not(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    for path in ("/api/status", "/api/signals", "/api/positions"):
        assert client.get(path).status_code == 401
    assert client.get("/api/status", headers={"Authorization": "Bearer faux"}).status_code == 401
    assert client.post("/api/signals/1/approve").status_code == 401
    assert client.put("/api/settings/mode", json={"mode": "auto"}).status_code == 401


def test_login_needs_password_and_code(client):
    code = pyotp.TOTP(TOTP_SECRET).now()
    assert client.post("/api/auth/login", json={"password": "faux", "code": code}).status_code == 401
    assert client.post("/api/auth/login", json={"password": PASSWORD, "code": "000000"}).status_code == 401


def test_code_cannot_be_reused(auth_settings):
    auth = AuthService(auth_settings)
    now = utcnow()
    code = pyotp.TOTP(TOTP_SECRET).at(now)
    assert auth.verify_token(auth.login(PASSWORD, code, now))
    with pytest.raises(AuthError) as error:
        auth.login(PASSWORD, code, now)
    assert error.value.status_code == 401
    later = now + timedelta(seconds=30)
    assert auth.login(PASSWORD, pyotp.TOTP(TOTP_SECRET).at(later), later)


def test_lockout_after_repeated_failures(auth_settings):
    auth = AuthService(auth_settings)
    code = pyotp.TOTP(TOTP_SECRET).at(NOW)
    for _ in range(5):
        with pytest.raises(AuthError):
            auth.login("faux", code, NOW)
    with pytest.raises(AuthError) as error:
        auth.login(PASSWORD, code, NOW)
    assert error.value.status_code == 429
    later = NOW + timedelta(minutes=16)
    assert auth.login(PASSWORD, pyotp.TOTP(TOTP_SECRET).at(later), later)


def test_expired_token_is_refused(auth_settings):
    auth = AuthService(auth_settings)
    past = utcnow() - timedelta(days=2)
    assert not auth.verify_token(auth.login(PASSWORD, pyotp.TOTP(TOTP_SECRET).at(past), past))


def test_without_auth_config_everything_is_closed(settings, session_factory):
    client = TestClient(create_app(settings, session_factory))
    assert client.post("/api/auth/login", json={"password": "", "code": ""}).status_code == 503
    assert client.get("/api/status").status_code == 401


def test_status_and_mode(client, headers):
    assert client.get("/api/status", headers=headers).json()["mode"] == "manual"
    assert client.put("/api/settings/mode", json={"mode": "auto"}, headers=headers).status_code == 200
    assert client.get("/api/status", headers=headers).json()["mode"] == "auto"
    assert client.put("/api/settings/mode", json={"mode": "live"}, headers=headers).status_code == 422


def test_approve_and_reject(client, headers, session_factory):
    first = add_signal(session_factory, created_at=utcnow())
    second = add_signal(session_factory, created_at=utcnow(), candle_time=NOW + timedelta(hours=4))

    assert client.get("/api/status", headers=headers).json()["pending_signals"] == 2
    assert client.post(f"/api/signals/{first}/approve", headers=headers).json()["status"] == SignalStatus.APPROVED
    assert client.post(f"/api/signals/{first}/approve", headers=headers).status_code == 409
    assert client.post(f"/api/signals/{second}/reject", headers=headers).json()["status"] == SignalStatus.REJECTED
    assert client.post("/api/signals/999/approve", headers=headers).status_code == 404
    assert [s["id"] for s in client.get("/api/signals", headers=headers).json()] == [second, first]


def test_expired_signal_cannot_be_approved(client, headers, session_factory):
    old = add_signal(session_factory, created_at=utcnow() - timedelta(hours=9))
    assert client.post(f"/api/signals/{old}/approve", headers=headers).status_code == 409
    assert client.get("/api/signals", headers=headers).json()[0]["status"] == SignalStatus.EXPIRED


def test_close_position(client, headers, session_factory):
    with session_factory() as session:
        position = Position(symbol="ETH/USDT", entry_price=100.0, quantity=0.2, cost=20.0, stop_loss=95.0, take_profit=110.0)
        session.add(position)
        session.commit()
        position_id = position.id

    body = client.post(f"/api/positions/{position_id}/close", headers=headers).json()
    assert body["close_requested"] is True
    assert body["opened_at"].endswith("Z") or body["opened_at"].endswith("+00:00")
    assert len(client.get("/api/positions?status=open", headers=headers).json()) == 1
    assert client.get("/api/positions?status=closed", headers=headers).json() == []
