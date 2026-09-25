from datetime import UTC, datetime

import jwt
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from core.settings import Settings
from service.access import authenticate, create_access_token


def test_no_auth_secret(mock_settings, mock_agent, test_client):
    """Test that when AUTH_SECRET is not set, all requests are allowed"""
    mock_settings.AUTH_SECRET = None
    response = test_client.post(
        "/invoke",
        json={"message": "test"},
        headers={"Authorization": "Bearer any-token"},
    )
    assert response.status_code == 200

    # Should also work without any auth header
    response = test_client.post("/invoke", json={"message": "test"})
    assert response.status_code == 200


def test_auth_secret_correct(mock_settings, mock_agent, test_client):
    """Test that when AUTH_SECRET is set, requests with correct token are allowed"""
    mock_settings.AUTH_SECRET = SecretStr("test-secret")
    response = test_client.post(
        "/invoke",
        json={"message": "test"},
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 200


def test_auth_secret_incorrect(mock_settings, mock_agent, test_client):
    """Test that when AUTH_SECRET is set, requests with wrong token are rejected"""
    mock_settings.AUTH_SECRET = SecretStr("test-secret")
    response = test_client.post(
        "/invoke",
        json={"message": "test"},
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert response.status_code == 401

    # Should also reject requests with no auth header
    response = test_client.post("/invoke", json={"message": "test"})
    assert response.status_code == 401


def test_app_access_token_lasts_fifteen_days_and_expiry_is_enforced():
    config = Settings(_env_file=None, APP_TOKEN_SECRET=SecretStr("x" * 32))
    token, expires_at = create_access_token("user-123", config)
    claims = jwt.decode(
        token,
        config.APP_TOKEN_SECRET.get_secret_value(),
        algorithms=["HS256"],
        audience="agent-service-app",
        issuer="agent-service-toolkit",
    )

    assert expires_at == claims["exp"]
    assert claims["exp"] - claims["iat"] == 15 * 24 * 60 * 60
    assert authenticate(f"Bearer {token}", config).user_id == "user-123"

    expired_claims = {**claims, "exp": int(datetime.now(UTC).timestamp()) - 1}
    expired_token = jwt.encode(
        expired_claims,
        config.APP_TOKEN_SECRET.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as exc_info:
        authenticate(f"Bearer {expired_token}", config)
    assert exc_info.value.status_code == 401
