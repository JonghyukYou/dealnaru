from datetime import timedelta

import bcrypt
from fastapi.testclient import TestClient

from app import main, settings
from app.database import SessionLocal
from app.models import Game, User, UserSession
from app.services import auth_service, email_service, public_security
from app.utils.time import utc_now
from test_api_smoke import client, reset_database  # noqa: F401


def test_secure_cookie_and_server_side_logout(monkeypatch, reset_database):
    monkeypatch.setattr(main, "PRODUCTION", True)
    monkeypatch.setattr(auth_service, "PRODUCTION", True)
    with TestClient(main.app, base_url="https://dealnaru.life") as browser:
        res = browser.post("/api/auth/login", json={"email": "user@test.local", "password": "user-pass-1234"})
        assert res.status_code == 200 and "access_token" not in res.json()
        cookie = res.headers["set-cookie"]
        assert cookie.startswith("__Host-dealnaru_session=")
        assert all(flag in cookie for flag in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"))
        assert "Domain=" not in cookie and res.headers["Cache-Control"] == "no-store"
        assert browser.get("/api/auth/me").json()["email"] == "user@test.local"
        token = browser.cookies.get(auth_service.cookie_name())
        with SessionLocal() as db:
            assert db.get(UserSession, token) is None
            assert db.get(UserSession, auth_service.hash_email_verification_token(token)) is not None
        assert browser.get("/api/admin/crawling-logs").status_code == 403
        assert browser.post("/api/auth/logout", headers={"Origin": "https://example.org"}).status_code == 403
        assert browser.post("/api/auth/logout").status_code == 204
        assert browser.get("/api/auth/me").status_code == 401
        browser.cookies.set(auth_service.cookie_name(), token)
        assert browser.get("/api/auth/me").status_code == 401


def test_production_does_not_allow_dev_price_writes_or_reset(monkeypatch, client):
    monkeypatch.setattr(main, "PRODUCTION", True)
    for method, path in [
        ("POST", "/api/dev/games/1/simulate-price"), ("POST", "/api/admin/collect-price"),
        ("DELETE", "/api/admin/dev/remove-sample-games"), ("DELETE", "/api/admin/dev/reset-collected-data"),
    ]:
        assert client.request(method, path, json={}).status_code == 404


def test_register_verify_and_reuse_protection(monkeypatch, client):
    messages = []
    monkeypatch.setattr(main, "PRODUCTION", True)
    monkeypatch.setattr(main, "send_verification_email",
                        lambda **kwargs: messages.append(kwargs) or {"dev_verify_url": "test-only"})
    payload = {"email": "newuser@domain.test", "password": "안전한비밀번호123456", "nickname": "테스트가입"}
    # A real address shape only; the transport is mocked.
    payload["email"] = "newuser@proton.me"
    assert client.post("/api/auth/register", json=payload).status_code == 400
    response = client.post("/api/auth/register", json={**payload, "accept_terms": True, "age_confirmed": True})
    assert response.status_code == 200 and "dev_verify_url" not in response.json()
    assert client.post("/api/auth/login", json=payload).status_code == 403
    assert client.post("/api/auth/resend-verification", json={"email": payload["email"]}).status_code == 200
    assert len(messages) == 1
    body = {"token": messages[0]["token"]}
    assert client.post("/api/auth/verify-email", json=body).status_code == 200
    assert client.post("/api/auth/verify-email", json=body).status_code == 400
    with SessionLocal() as db:
        row = db.query(User).filter_by(email=payload["email"]).one()
        assert row.email_verified and row.terms_version == "1.1" and row.terms_accepted_at
        assert row.email_verify_token_hash is None and row.password_hash.startswith("$argon2id$")
    assert client.post("/api/auth/login", json=payload).status_code == 200


def test_invalid_input_does_not_echo_password_and_bad_email_is_rejected(client):
    short = "x0xy"
    res = client.post("/api/auth/register", json={"email": "user@example.org", "password": short, "nickname": "신규"})
    assert res.status_code == 422 and short not in res.text
    res = client.post("/api/auth/register", json={"email": "email", "password": "long-password-123", "nickname": "신규"})
    assert res.status_code == 400


def test_mail_failure_leaves_account_available_to_recover(monkeypatch, client):
    def disconnected(**_kwargs):
        raise RuntimeError("provider detail")
    monkeypatch.setattr(main, "send_verification_email", disconnected)
    email = "recover@proton.me"
    response = client.post("/api/auth/register", json={"email": email, "password": "long-password-123", "nickname": "복구계정"})
    assert response.status_code == 200 and response.json()["mail_sent"] is False
    with SessionLocal() as db:
        row = db.query(User).filter_by(email=email).one()
        row.email_verify_sent_at = utc_now() - timedelta(minutes=2)
        db.commit()
    response = client.post("/api/auth/resend-verification", json={"email": email})
    assert response.status_code == 200
    assert "provider detail" not in response.text and "dev_verify_url" not in response.text


def test_rate_limits_admit_then_bound_repeat_requests(monkeypatch, client):
    monkeypatch.setattr(public_security, "PRODUCTION", True)
    monkeypatch.setattr(public_security, "budget", public_security.RequestBudget())
    for _ in range(5):
        assert client.post("/api/auth/resend-verification", json={"email": "unregistered@proton.me"}).status_code == 200
    response = client.post("/api/auth/resend-verification", json={"email": "unregistered@proton.me"})
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0


def test_legacy_bcrypt_upgrades_without_account_reset(client):
    password = "legacy-password-789"
    with SessionLocal() as db:
        row = db.query(User).filter_by(email="user@test.local").one()
        row.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()
        db.commit()
    assert client.post("/api/auth/login", json={"email": "user@test.local", "password": password}).status_code == 200
    with SessionLocal() as db:
        assert db.query(User).filter_by(email="user@test.local").one().password_hash.startswith("$argon2id$")


def test_wishlist_accepts_zero_and_rejects_invalid_database_values(client):
    assert client.post("/api/auth/login", json={"email": "user@test.local", "password": "user-pass-1234"}).status_code == 200
    with SessionLocal() as db:
        gid = db.query(Game.id).filter_by(title="Test Game").scalar()
    for invalid in (-1, 1.5, 2147483648, "42"):
        assert client.put(f"/api/wishlist/games/{gid}", json={"target_price": invalid}).status_code == 422
    assert client.put(f"/api/wishlist/games/{gid}", json={"target_price": 0}).status_code == 200
    assert any(row["target_price"] == 0 for row in client.get("/api/wishlist").json())


def test_alert_email_omits_invalid_link(monkeypatch):
    monkeypatch.setattr(email_service, "send_email", lambda **kwargs: kwargs)
    msg = email_service.send_target_price_email("test@proton.me", "테스트", "Game", "Steam", 100, 200,
                                               game_url="http://store.steampowered.com/app/620")
    assert "할인 페이지 확인하기" not in msg["html"]
    assert "/verify-email#token=" in email_service.build_verify_url("local-test")


def test_public_config_rejects_dev_mail(monkeypatch):
    monkeypatch.setattr(settings, "PRODUCTION", True)
    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://dealnaru.life")
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ("https://dealnaru.life",))
    monkeypatch.setattr(settings, "EMAIL_DEV_MODE", True)
    import pytest
    with pytest.raises(RuntimeError, match="개발 메일"):
        settings.validate_production_settings()
