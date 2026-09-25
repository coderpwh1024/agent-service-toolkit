import logging
import socket
from pathlib import Path
from typing import Any

import yaml
from v2.nacos import (
    ClientConfig,
    ClientConfigBuilder,
    ConfigParam,
    DeregisterInstanceParam,
    GRPCConfig,
    Instance,
    ListInstanceParam,
    NacosConfigService,
    NacosNamingService,
    RegisterInstanceParam,
)

from core.settings import Settings

logger = logging.getLogger(__name__)

_NACOS_BOOTSTRAP_FIELDS = {
    "APP_ENV",
    "GRACEFUL_SHUTDOWN_TIMEOUT",
    "HOST",
    "LOG_LEVEL",
    "MODE",
    "PORT",
}
_REMOTE_STORAGE_FIELDS = {
    "POSTGRES_DB",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "REDIS_URL",
}
_MAIL_FIELDS = {
    "host": "SMTP_HOST",
    "password": "SMTP_PASSWORD",
    "port": "SMTP_PORT",
    "username": "SMTP_USERNAME",
}
_NESTED_SETTINGS: dict[str, Any] = {
    "models": {
        "dashscope": {
            "api_key": "DASHSCOPE_API_KEY",
            "base_url": "DASHSCOPE_BASE_URL",
            "embedding_model": "DASHSCOPE_EMBEDDING_MODEL",
        },
    },
    "authentication": {
        "admin_secret": "AUTH_SECRET",
        "service_account_id": "AUTH_SERVICE_ACCOUNT_ID",
        "app_token": {
            "secret": "APP_TOKEN_SECRET",
            "ttl_seconds": "APP_TOKEN_TTL_SECONDS",
        },
        "email": {
            "enabled": "EMAIL_AUTH_ENABLED",
            "smtp": {
                "host": "SMTP_HOST",
                "port": "SMTP_PORT",
                "username": "SMTP_USERNAME",
                "password": "SMTP_PASSWORD",
                "from_email": "SMTP_FROM_EMAIL",
                "use_tls": "SMTP_USE_TLS",
                "use_ssl": "SMTP_USE_SSL",
            },
        },
    },
    "storage": {
        "database_type": "DATABASE_TYPE",
        "redis": {
            "url": "REDIS_URL",
        },
        "postgres": {
            "user": "POSTGRES_USER",
            "password": "POSTGRES_PASSWORD",
            "host": "POSTGRES_HOST",
            "port": "POSTGRES_PORT",
            "database": "POSTGRES_DB",
            "application_name": "POSTGRES_APPLICATION_NAME",
            "pool": {
                "min_connections": "POSTGRES_MIN_CONNECTIONS_PER_POOL",
                "max_connections": "POSTGRES_MAX_CONNECTIONS_PER_POOL",
            },
        },
        "qiniu": {
            "ak": "QINIU_ACCESS_KEY",
            "sk": "QINIU_SECRET_KEY",
            "bucket_name": "QINIU_BUCKET_NAME",
            "public_base_url": "QINIU_PUBLIC_BASE_URL",
            "upload_token_ttl_seconds": "QINIU_UPLOAD_TOKEN_TTL_SECONDS",
            "avatar_max_bytes": "QINIU_AVATAR_MAX_BYTES",
        },
    },
    "rag": {
        "collection_name": "RAG_COLLECTION_NAME",
        "top_k": "RAG_TOP_K",
    },
    "voice": {
        "enabled": "VOICE_ENABLED",
        "realtime": {
            "url": "VOICE_REALTIME_URL",
            "proxy": "VOICE_REALTIME_PROXY",
            "stt_model": "VOICE_REALTIME_STT_MODEL",
            "tts_model": "VOICE_REALTIME_TTS_MODEL",
            "voices": "VOICE_REALTIME_VOICES",
            "upstream_timeout": "VOICE_UPSTREAM_TIMEOUT",
        },
        "session": {
            "max_sessions": "VOICE_MAX_SESSIONS",
            "duration_seconds": "VOICE_SESSION_SECONDS",
            "idle_seconds": "VOICE_IDLE_SECONDS",
            "queue_size": "VOICE_QUEUE_SIZE",
        },
        "vad": {
            "silence_ms": "VOICE_VAD_SILENCE_MS",
            "threshold": "VOICE_VAD_THRESHOLD",
            "client_rms_dbfs": "VOICE_CLIENT_VAD_RMS_DBFS",
            "client_frames": "VOICE_CLIENT_VAD_FRAMES",
        },
        "wake": {
            "enabled": "VOICE_WAKE_ENABLED",
            "word": "VOICE_WAKE_WORD",
            "confirm_with_asr": "VOICE_WAKE_CONFIRM_WITH_ASR",
            "pre_roll_ms": "VOICE_WAKE_PRE_ROLL_MS",
            "kws_score": "VOICE_WAKE_KWS_SCORE",
            "kws_threshold": "VOICE_WAKE_KWS_THRESHOLD",
        },
        "audio_metrics_seconds": "VOICE_AUDIO_METRICS_SECONDS",
    },
}
_MISSING = object()


def _settings_values(config: Settings) -> dict[str, Any]:
    return {name: getattr(config, name) for name in type(config).model_fields}


def _require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(name, str) for name in value):
        raise ValueError(f"Nacos {path} must be a YAML mapping with string keys")
    return value


def _require_boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"Nacos {path} must be a boolean")
    return value


def _reject_unknown_fields(value: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unknown Nacos {path} settings: {', '.join(sorted(unknown))}")


def _flatten_nested_settings(
    value: Any,
    schema: dict[str, Any],
    path: str,
) -> dict[str, Any]:
    section = _require_mapping(value, path)
    _reject_unknown_fields(section, set(schema), path)
    flattened: dict[str, Any] = {}
    for name, item in section.items():
        target = schema[name]
        if isinstance(target, str):
            flattened[target] = item
            continue
        flattened.update(_flatten_nested_settings(item, target, f"{path}.{name}"))
    return flattened


def _normalize_nested_settings(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for section_name, schema in _NESTED_SETTINGS.items():
        if section_name not in normalized:
            continue
        mapped = _flatten_nested_settings(
            normalized.pop(section_name),
            schema,
            section_name,
        )
        for setting_name, value in mapped.items():
            if setting_name in normalized and normalized[setting_name] != value:
                raise ValueError(
                    f"Nacos {section_name} configuration conflicts with {setting_name}"
                )
            normalized[setting_name] = value
    return normalized


def _mail_transport_settings(mail: dict[str, Any]) -> dict[str, Any]:
    properties = _require_mapping(mail.get("properties", {}), "mail.properties")
    _reject_unknown_fields(properties, {"mail"}, "mail.properties")
    mail_properties = _require_mapping(properties.get("mail", {}), "mail.properties.mail")
    _reject_unknown_fields(mail_properties, {"smtp"}, "mail.properties.mail")
    smtp = _require_mapping(mail_properties.get("smtp", {}), "mail.properties.mail.smtp")
    _reject_unknown_fields(
        smtp,
        {"auth", "socketFactory", "ssl", "starttls"},
        "mail.properties.mail.smtp",
    )

    if "auth" in smtp:
        _require_boolean(smtp["auth"], "mail.properties.mail.smtp.auth")

    ssl_settings = _require_mapping(smtp.get("ssl", {}), "mail.properties.mail.smtp.ssl")
    _reject_unknown_fields(ssl_settings, {"enable"}, "mail.properties.mail.smtp.ssl")
    starttls_settings = _require_mapping(
        smtp.get("starttls", {}), "mail.properties.mail.smtp.starttls"
    )
    _reject_unknown_fields(
        starttls_settings,
        {"enable", "required"},
        "mail.properties.mail.smtp.starttls",
    )
    socket_factory = _require_mapping(
        smtp.get("socketFactory", {}), "mail.properties.mail.smtp.socketFactory"
    )
    _reject_unknown_fields(
        socket_factory,
        {"class", "fallback", "port"},
        "mail.properties.mail.smtp.socketFactory",
    )
    if "fallback" in socket_factory:
        _require_boolean(
            socket_factory["fallback"],
            "mail.properties.mail.smtp.socketFactory.fallback",
        )

    ssl_enabled = None
    if "enable" in ssl_settings:
        ssl_enabled = _require_boolean(
            ssl_settings["enable"], "mail.properties.mail.smtp.ssl.enable"
        )
    starttls_enabled = None
    if "enable" in starttls_settings:
        starttls_enabled = _require_boolean(
            starttls_settings["enable"], "mail.properties.mail.smtp.starttls.enable"
        )
    if "required" in starttls_settings:
        required = _require_boolean(
            starttls_settings["required"], "mail.properties.mail.smtp.starttls.required"
        )
        if required and starttls_enabled is False:
            raise ValueError("Nacos mail STARTTLS cannot be required when it is disabled")
        if required and starttls_enabled is None:
            starttls_enabled = True

    if ssl_enabled and starttls_enabled:
        raise ValueError("Nacos mail SSL and STARTTLS cannot both be enabled")
    if ssl_enabled is None and starttls_enabled is None:
        return {}
    return {
        "SMTP_USE_SSL": ssl_enabled or False,
        "SMTP_USE_TLS": starttls_enabled or False,
    }


def _normalize_mail_settings(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    mail_value = normalized.pop("mail", _MISSING)
    if "spring" in normalized:
        spring = _require_mapping(normalized.pop("spring"), "spring")
        _reject_unknown_fields(spring, {"mail"}, "spring")
        if mail_value is not _MISSING:
            raise ValueError("Nacos configuration cannot contain both mail and spring.mail")
        if "mail" not in spring:
            raise ValueError("Nacos spring configuration must contain mail")
        mail_value = spring["mail"]
    if mail_value is _MISSING:
        return normalized

    mail = _require_mapping(mail_value, "mail")
    _reject_unknown_fields(mail, set(_MAIL_FIELDS) | {"default-encoding", "properties"}, "mail")
    encoding = mail.get("default-encoding")
    if encoding is not None and (
        not isinstance(encoding, str) or encoding.lower().replace("-", "") != "utf8"
    ):
        raise ValueError("Nacos mail.default-encoding must be UTF-8")

    mapped = {setting: mail[name] for name, setting in _MAIL_FIELDS.items() if name in mail}
    mapped.update(_mail_transport_settings(mail))
    for name, value in mapped.items():
        if name in normalized and normalized[name] != value:
            raise ValueError(f"Nacos mail configuration conflicts with {name}")
        normalized[name] = value
    if "SMTP_FROM_EMAIL" not in normalized and "username" in mail:
        normalized["SMTP_FROM_EMAIL"] = mail["username"]
    return normalized


def apply_remote_settings(
    config: Settings,
    content: str,
    *,
    required_fields: set[str] | None = None,
) -> list[str]:
    """Validate and apply a Nacos YAML mapping to the shared Settings instance."""
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError("Nacos configuration must be valid YAML") from exc

    if not isinstance(payload, dict):
        raise ValueError("Nacos configuration must be a YAML mapping")
    if any(not isinstance(name, str) for name in payload):
        raise ValueError("Nacos configuration keys must be strings")
    payload = _normalize_nested_settings(payload)
    payload = _normalize_mail_settings(payload)

    missing = (required_fields or set()) - set(payload)
    if missing:
        raise ValueError(
            "Nacos configuration is missing required settings: " + ", ".join(sorted(missing))
        )

    setting_names = set(type(config).model_fields)
    unknown = set(payload) - setting_names
    if unknown:
        raise ValueError(f"Unknown Nacos settings: {', '.join(sorted(unknown))}")

    bootstrap_fields = _NACOS_BOOTSTRAP_FIELDS | {
        name for name in setting_names if name.startswith("NACOS_")
    }
    forbidden = set(payload) & bootstrap_fields
    if forbidden:
        raise ValueError(
            "Nacos cannot override bootstrap settings: " + ", ".join(sorted(forbidden))
        )

    values = _settings_values(config)
    values.update(payload)
    values["AVAILABLE_MODELS"] = set()
    if "DEFAULT_MODEL" not in payload and "DEFAULT_MODEL" not in config.model_fields_set:
        values["DEFAULT_MODEL"] = None

    validated = type(config)(_env_file=None, **values)
    validated.require_model_provider()
    validated.require_voice_configuration()
    for name in type(config).model_fields:
        setattr(config, name, getattr(validated, name))
    return sorted(payload)


class NacosIntegration:
    """Own the Nacos clients and the service instance lifecycle."""

    def __init__(self, config: Settings) -> None:
        self.config = config
        self.config_client: NacosConfigService | None = None
        self.naming_client: NacosNamingService | None = None
        self._registration: RegisterInstanceParam | None = None

    def _client_config(self) -> ClientConfig:
        _prepare_runtime_directory(self.config.NACOS_CACHE_DIR)
        _prepare_runtime_directory(self.config.NACOS_LOG_DIR)
        builder = (
            ClientConfigBuilder()
            .server_address(self.config.NACOS_SERVER_ADDR)
            .namespace_id(self.config.NACOS_NAMESPACE_ID)
            .context_path(self.config.NACOS_CONTEXT_PATH)
            .cache_dir(self.config.NACOS_CACHE_DIR)
            .log_dir(self.config.NACOS_LOG_DIR)
            .log_level(logging.WARNING)
            .grpc_config(GRPCConfig(port_offset=self.config.NACOS_GRPC_PORT_OFFSET))
        )
        if self.config.NACOS_USERNAME and self.config.NACOS_PASSWORD:
            builder.username(self.config.NACOS_USERNAME).password(
                self.config.NACOS_PASSWORD.get_secret_value()
            )
        return builder.build()

    async def load_remote_config(self) -> None:
        if not self.config.NACOS_ENABLED:
            return
        if not self.config.NACOS_CONFIG_DATA_ID:
            if self.config.NACOS_STORAGE_CONFIG_REQUIRED:
                raise RuntimeError(
                    "NACOS_CONFIG_DATA_ID is required when Nacos storage configuration is mandatory"
                )
            return

        self.config_client = await NacosConfigService.create_config_service(self._client_config())
        content = await self.config_client.get_config(
            ConfigParam(
                data_id=self.config.NACOS_CONFIG_DATA_ID,
                group=self.config.NACOS_GROUP_NAME,
            )
        )
        if not content:
            raise RuntimeError(
                f"Nacos configuration {self.config.NACOS_GROUP_NAME}/"
                f"{self.config.NACOS_CONFIG_DATA_ID} is empty or unavailable"
            )
        required_fields = (
            _REMOTE_STORAGE_FIELDS if self.config.NACOS_STORAGE_CONFIG_REQUIRED else None
        )
        updated = apply_remote_settings(
            self.config,
            content,
            required_fields=required_fields,
        )
        logger.info("Applied Nacos startup configuration fields: %s", ", ".join(updated))

    async def register_service(self) -> None:
        if not self.config.NACOS_ENABLED:
            return

        self.naming_client = await NacosNamingService.create_naming_service(self._client_config())
        if not await self.naming_client.server_health():
            raise RuntimeError("Nacos naming service is unavailable")
        if not self.config.NACOS_REGISTER_SERVICE:
            return

        registration = RegisterInstanceParam(
            service_name=self.config.NACOS_SERVICE_NAME,
            group_name=self.config.NACOS_GROUP_NAME,
            ip=self.config.NACOS_SERVICE_IP or _local_ip(),
            port=self.config.NACOS_SERVICE_PORT or self.config.PORT,
            cluster_name=self.config.NACOS_CLUSTER_NAME,
            metadata=self.config.NACOS_METADATA,
            ephemeral=True,
        )
        if not await self.naming_client.register_instance(registration):
            raise RuntimeError(f"Nacos rejected registration for {registration.service_name}")
        self._registration = registration
        logger.info(
            "Registered Nacos service %s at %s:%s",
            registration.service_name,
            registration.ip,
            registration.port,
        )

    async def list_instances(
        self,
        service_name: str,
        *,
        group_name: str | None = None,
        healthy_only: bool | None = True,
    ) -> list[Instance]:
        if self.naming_client is None:
            raise RuntimeError("Nacos naming client is not running")
        return await self.naming_client.list_instances(
            ListInstanceParam(
                service_name=service_name,
                group_name=group_name or self.config.NACOS_GROUP_NAME,
                healthy_only=healthy_only,
            )
        )

    async def shutdown(self) -> None:
        registration = self._registration
        self._registration = None
        if self.naming_client is not None:
            try:
                if registration is not None:
                    removed = await self.naming_client.deregister_instance(
                        DeregisterInstanceParam(
                            service_name=registration.service_name,
                            group_name=registration.group_name,
                            ip=registration.ip,
                            port=registration.port,
                            cluster_name=registration.cluster_name,
                            ephemeral=registration.ephemeral,
                        )
                    )
                    if not removed:
                        logger.warning(
                            "Nacos did not confirm deregistration for %s",
                            registration.service_name,
                        )
            except Exception:
                logger.exception("Failed to deregister the Nacos service instance")
            finally:
                try:
                    await self.naming_client.shutdown()
                except Exception:
                    logger.exception("Failed to shut down the Nacos naming client")
                self.naming_client = None

        if self.config_client is not None:
            try:
                await self.config_client.shutdown()
            except Exception:
                logger.exception("Failed to shut down the Nacos configuration client")
            self.config_client = None


def _local_ip() -> str:
    return socket.gethostbyname(socket.gethostname())


def _prepare_runtime_directory(path: str) -> None:
    directory = Path(path)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
