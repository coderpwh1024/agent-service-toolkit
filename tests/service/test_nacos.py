from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from core.settings import Settings
from service import nacos


def make_settings(**overrides) -> Settings:
    return Settings(USE_FAKE_MODEL=True, _env_file=None, **overrides)


def test_apply_remote_settings_validates_and_updates_shared_settings() -> None:
    config = make_settings(RAG_TOP_K=5)

    updated = nacos.apply_remote_settings(
        config,
        "RAG_TOP_K: 9\nRAG_COLLECTION_NAME: nacos-collection\n",
    )

    assert updated == ["RAG_COLLECTION_NAME", "RAG_TOP_K"]
    assert config.RAG_TOP_K == 9
    assert config.RAG_COLLECTION_NAME == "nacos-collection"


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- item", "YAML mapping"),
        ("UNKNOWN_SETTING: true", "Unknown Nacos settings"),
        ("PORT: 9000", "bootstrap settings"),
        ("NACOS_SERVICE_NAME: other", "bootstrap settings"),
        ("1: value", "keys must be strings"),
    ],
)
def test_apply_remote_settings_rejects_invalid_payloads(content: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        nacos.apply_remote_settings(make_settings(), content)


@pytest.mark.asyncio
async def test_load_remote_config(monkeypatch, tmp_path) -> None:
    config = make_settings(
        NACOS_ENABLED=True,
        NACOS_CONFIG_DATA_ID="agent-service-toolkit.yaml",
        NACOS_STORAGE_CONFIG_REQUIRED=False,
        NACOS_USERNAME="nacos",
        NACOS_PASSWORD=SecretStr("secret"),
        NACOS_CACHE_DIR=str(tmp_path / "cache"),
        NACOS_LOG_DIR=str(tmp_path / "logs"),
    )
    config_client = AsyncMock()
    config_client.get_config.return_value = "RAG_TOP_K: 7\n"
    create_config_service = AsyncMock(return_value=config_client)
    monkeypatch.setattr(
        nacos.NacosConfigService,
        "create_config_service",
        create_config_service,
    )

    integration = nacos.NacosIntegration(config)
    await integration.load_remote_config()

    assert config.RAG_TOP_K == 7
    client_config = create_config_service.await_args.args[0]
    assert client_config.server_list == ["127.0.0.1:8848"]
    assert client_config.username == "nacos"
    assert client_config.password == "secret"
    await integration.shutdown()
    config_client.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_discover_and_deregister_service(monkeypatch, tmp_path) -> None:
    config = make_settings(
        NACOS_ENABLED=True,
        NACOS_STORAGE_CONFIG_REQUIRED=False,
        NACOS_SERVICE_IP="10.0.0.7",
        NACOS_SERVICE_PORT=9000,
        NACOS_METADATA={"protocol": "http"},
        NACOS_CACHE_DIR=str(tmp_path / "cache"),
        NACOS_LOG_DIR=str(tmp_path / "logs"),
    )
    naming_client = AsyncMock()
    naming_client.server_health.return_value = True
    naming_client.register_instance.return_value = True
    naming_client.deregister_instance.return_value = True
    naming_client.list_instances.return_value = []
    create_naming_service = AsyncMock(return_value=naming_client)
    monkeypatch.setattr(
        nacos.NacosNamingService,
        "create_naming_service",
        create_naming_service,
    )

    integration = nacos.NacosIntegration(config)
    await integration.register_service()
    assert await integration.list_instances("dependency") == []
    await integration.shutdown()

    registration = naming_client.register_instance.await_args.args[0]
    assert registration.service_name == "agent-service-toolkit"
    assert registration.ip == "10.0.0.7"
    assert registration.port == 9000
    assert registration.metadata == {"protocol": "http"}
    deregistration = naming_client.deregister_instance.await_args.args[0]
    assert deregistration.service_name == registration.service_name
    assert deregistration.ip == registration.ip
    assert deregistration.port == registration.port
    naming_client.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_fails_when_nacos_is_unavailable(monkeypatch, tmp_path) -> None:
    config = make_settings(
        NACOS_ENABLED=True,
        NACOS_STORAGE_CONFIG_REQUIRED=False,
        NACOS_CACHE_DIR=str(tmp_path / "cache"),
        NACOS_LOG_DIR=str(tmp_path / "logs"),
    )
    naming_client = AsyncMock()
    naming_client.server_health.return_value = False
    create_naming_service = AsyncMock(return_value=naming_client)
    monkeypatch.setattr(
        nacos.NacosNamingService,
        "create_naming_service",
        create_naming_service,
    )

    integration = nacos.NacosIntegration(config)
    with pytest.raises(RuntimeError, match="naming service is unavailable"):
        await integration.register_service()
    await integration.shutdown()
    naming_client.shutdown.assert_awaited_once()


def test_remote_storage_config_must_be_complete() -> None:
    config = make_settings()

    with pytest.raises(ValueError, match="POSTGRES_DB"):
        nacos.apply_remote_settings(
            config,
            "REDIS_URL: redis://remote:6379/0\n",
            required_fields=nacos._REMOTE_STORAGE_FIELDS,
        )


def test_apply_remote_settings_rejects_malformed_yaml() -> None:
    with pytest.raises(ValueError, match="valid YAML"):
        nacos.apply_remote_settings(make_settings(), "REDIS_URL: [")


def test_remote_email_settings_override_local_values() -> None:
    config = make_settings(
        APP_TOKEN_SECRET="local-token-secret-that-is-long-enough",
        SMTP_HOST="local-smtp",
        SMTP_PORT=587,
        SMTP_USERNAME="local@example.com",
        SMTP_PASSWORD="local-password",
        SMTP_FROM_EMAIL="local@example.com",
        SMTP_USE_TLS=True,
        SMTP_USE_SSL=False,
    )

    nacos.apply_remote_settings(
        config,
        """APP_TOKEN_SECRET: remote-token-secret-that-is-long-enough
SMTP_HOST: smtp.example.com
SMTP_PORT: 465
SMTP_USERNAME: mailer@example.com
SMTP_PASSWORD: remote-password
SMTP_FROM_EMAIL: mailer@example.com
SMTP_USE_TLS: false
SMTP_USE_SSL: true
""",
    )

    assert config.APP_TOKEN_SECRET == SecretStr("remote-token-secret-that-is-long-enough")
    assert config.SMTP_HOST == "smtp.example.com"
    assert config.SMTP_PORT == 465
    assert config.SMTP_USERNAME == "mailer@example.com"
    assert config.SMTP_PASSWORD == SecretStr("remote-password")
    assert str(config.SMTP_FROM_EMAIL) == "mailer@example.com"
    assert config.SMTP_USE_TLS is False
    assert config.SMTP_USE_SSL is True


def test_remote_storage_config_overrides_local_values() -> None:
    config = make_settings(
        REDIS_URL="redis://local:6379/0",
        POSTGRES_USER="local-user",
        POSTGRES_PASSWORD="local-password",
        POSTGRES_HOST="local-postgres",
        POSTGRES_PORT=5432,
        POSTGRES_DB="local-db",
    )

    nacos.apply_remote_settings(
        config,
        """REDIS_URL: redis://remote:6379/0
POSTGRES_USER: remote-user
POSTGRES_PASSWORD: remote-password
POSTGRES_HOST: remote-postgres
POSTGRES_PORT: 6432
POSTGRES_DB: remote-db
""",
        required_fields=nacos._REMOTE_STORAGE_FIELDS,
    )

    assert config.REDIS_URL == SecretStr("redis://remote:6379/0")
    assert config.POSTGRES_USER == "remote-user"
    assert config.POSTGRES_PASSWORD == SecretStr("remote-password")
    assert config.POSTGRES_HOST == "remote-postgres"
    assert config.POSTGRES_PORT == 6432
    assert config.POSTGRES_DB == "remote-db"
