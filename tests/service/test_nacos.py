import os
from unittest.mock import AsyncMock, patch

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


def test_nacos_bootstrap_loads_model_provider_from_remote_config() -> None:
    with patch.dict(os.environ, {}, clear=True):
        config = Settings(
            _env_file=None,
            NACOS_ENABLED=True,
            NACOS_CONFIG_DATA_ID="agent-service-toolkit.yaml",
        )

    nacos.apply_remote_settings(config, "DASHSCOPE_API_KEY: remote-key\n")

    assert config.DASHSCOPE_API_KEY == SecretStr("remote-key")
    assert config.AVAILABLE_MODELS


def test_nacos_remote_config_must_resolve_model_provider() -> None:
    with patch.dict(os.environ, {}, clear=True):
        config = Settings(
            _env_file=None,
            NACOS_ENABLED=True,
            NACOS_CONFIG_DATA_ID="agent-service-toolkit.yaml",
        )

    with pytest.raises(ValueError, match="At least one LLM API key must be provided"):
        nacos.apply_remote_settings(config, "RAG_TOP_K: 7\n")


def test_nested_nacos_config_maps_sections_to_settings() -> None:
    config = make_settings()

    updated = nacos.apply_remote_settings(
        config,
        """models:
  dashscope:
    api_key: remote-key
authentication:
  email:
    enabled: true
storage:
  redis:
    url: redis://remote:6379/0
voice:
  enabled: true
  realtime:
    voices:
      - Cherry
  session:
    max_sessions: 12
  vad:
    silence_ms: 750
rag:
  top_k: 9
""",
    )

    assert updated == [
        "DASHSCOPE_API_KEY",
        "EMAIL_AUTH_ENABLED",
        "RAG_TOP_K",
        "REDIS_URL",
        "VOICE_ENABLED",
        "VOICE_MAX_SESSIONS",
        "VOICE_REALTIME_VOICES",
        "VOICE_VAD_SILENCE_MS",
    ]
    assert config.DASHSCOPE_API_KEY == SecretStr("remote-key")
    assert config.EMAIL_AUTH_ENABLED is True
    assert config.REDIS_URL == SecretStr("redis://remote:6379/0")
    assert config.VOICE_ENABLED is True
    assert [voice.id for voice in config.VOICE_REALTIME_VOICES] == ["Cherry"]
    assert config.VOICE_REALTIME_VOICES[0].name == "Cherry"
    assert config.VOICE_MAX_SESSIONS == 12
    assert config.VOICE_VAD_SILENCE_MS == 750
    assert config.RAG_TOP_K == 9


def test_complete_nested_nacos_yaml_is_accepted() -> None:
    config = make_settings()

    updated = nacos.apply_remote_settings(
        config,
        """models:
  dashscope:
    api_key: remote-key
    base_url: https://dashscope.example.com/v1
    embedding_model: embedding-model
authentication:
  admin_secret: remote-admin-secret
  service_account_id: 0
  app_token:
    secret: remote-app-token-secret-at-least-32-characters
    ttl_seconds: 1296000
  email:
    enabled: true
    smtp:
      host: smtp.example.com
      port: 465
      username: mailer@example.com
      password: remote-password
      from_email: mailer@example.com
      use_tls: false
      use_ssl: true
storage:
  database_type: postgres
  redis:
    url: redis://remote:6379/0
  postgres:
    user: remote-user
    password: remote-password
    host: remote-postgres
    port: 5432
    database: agent_service
    application_name: agent-service-toolkit
    pool:
      min_connections: 1
      max_connections: 1
  qiniu:
    ak: qiniu-access-key
    sk: qiniu-secret-key
    bucket_name: agent-service-toolkit-avatars
    public_base_url: https://cdn.example.com
    upload_token_ttl_seconds: 3600
    avatar_max_bytes: 5242880
rag:
  collection_name: remote-collection
  top_k: 5
voice:
  enabled: true
  realtime:
    url: wss://dashscope.example.com/realtime
    proxy: null
    stt_model: realtime-stt
    tts_model: realtime-tts
    voices:
      - id: Cherry
        name: 芊悦
        description: 阳光积极、亲切自然
      - id: Serena
        name: 苏瑶
        description: 温柔自然
    upstream_timeout: 30
  session:
    max_sessions: 8
    duration_seconds: 1800
    idle_seconds: 120
    queue_size: 64
  vad:
    silence_ms: 500
    threshold: 0.2
""",
        required_fields=nacos._REMOTE_STORAGE_FIELDS,
    )

    assert "VOICE_ENABLED" in updated
    assert config.VOICE_ENABLED is True
    assert [voice.id for voice in config.VOICE_REALTIME_VOICES] == ["Cherry", "Serena"]
    assert config.VOICE_REALTIME_VOICES[0].name == "芊悦"
    assert config.VOICE_REALTIME_VOICES[1].description == "温柔自然"
    assert config.POSTGRES_DB == "agent_service"
    assert config.QINIU_ACCESS_KEY == SecretStr("qiniu-access-key")
    assert config.QINIU_SECRET_KEY == SecretStr("qiniu-secret-key")
    assert config.QINIU_BUCKET_NAME == "agent-service-toolkit-avatars"
    assert config.APP_TOKEN_TTL_SECONDS == 1_296_000


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("voice:\n  unknown: true\n", "Unknown Nacos voice settings"),
        (
            "voice:\n  enabled: true\nVOICE_ENABLED: false\n",
            "conflicts with VOICE_ENABLED",
        ),
    ],
)
def test_nested_nacos_config_rejects_invalid_sections(content: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        nacos.apply_remote_settings(make_settings(), content)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("- item", "YAML mapping"),
        ("UNKNOWN_SETTING: true", "Unknown Nacos settings"),
        ("APP_ENV: test", "bootstrap settings"),
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


def test_spring_style_mail_settings_are_mapped_to_smtp_settings() -> None:
    config = make_settings()

    updated = nacos.apply_remote_settings(
        config,
        """mail:
  host: smtp.example.com
  port: 465
  username: mailer@example.com
  password: remote-password
  default-encoding: UTF-8
  properties:
    mail:
      smtp:
        ssl:
          enable: true
        socketFactory:
          fallback: false
          class: com.example.MailSocketFactory
""",
    )

    assert updated == [
        "SMTP_FROM_EMAIL",
        "SMTP_HOST",
        "SMTP_PASSWORD",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_USE_SSL",
        "SMTP_USE_TLS",
    ]
    assert config.SMTP_HOST == "smtp.example.com"
    assert config.SMTP_PORT == 465
    assert config.SMTP_USERNAME == "mailer@example.com"
    assert config.SMTP_PASSWORD == SecretStr("remote-password")
    assert str(config.SMTP_FROM_EMAIL) == "mailer@example.com"
    assert config.SMTP_USE_TLS is False
    assert config.SMTP_USE_SSL is True


def test_spring_mail_wrapper_and_starttls_are_supported() -> None:
    config = make_settings()

    nacos.apply_remote_settings(
        config,
        """spring:
  mail:
    host: smtp.example.com
    port: 587
    username: mailer@example.com
    password: remote-password
    properties:
      mail:
        smtp:
          starttls:
            required: true
""",
    )

    assert config.SMTP_USE_TLS is True
    assert config.SMTP_USE_SSL is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("mail:\n  unknown: value\n", "Unknown Nacos mail settings"),
        (
            "mail:\n  default-encoding: GBK\n",
            "mail.default-encoding must be UTF-8",
        ),
        (
            "mail:\n  host: nested\nSMTP_HOST: flat\n",
            "conflicts with SMTP_HOST",
        ),
        (
            """mail:
  properties:
    mail:
      smtp:
        ssl:
          enable: true
        starttls:
          enable: true
""",
            "SSL and STARTTLS cannot both be enabled",
        ),
    ],
)
def test_spring_style_mail_settings_reject_invalid_payloads(content: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        nacos.apply_remote_settings(make_settings(), content)


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
