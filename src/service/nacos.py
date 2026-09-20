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


def _settings_values(config: Settings) -> dict[str, Any]:
    return {name: getattr(config, name) for name in type(config).model_fields}


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
