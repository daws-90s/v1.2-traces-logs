"""Read-only container visibility via the Docker Engine API. Same socket
mount alloy already uses read-only in docker-compose.yml (`:ro`) — no new
privilege class introduced by adding this client.

Every method here maps to one of app.security.permissions.
ALLOWED_DOCKER_OPERATIONS. docker-py exposes exec_run/restart/stop/kill/
remove/update on a Container object; none of those methods are ever called
anywhere in this module (or anywhere else in this codebase) — see #22.
"""
import logging

import docker
from docker.errors import DockerException

from app.config import settings

logger = logging.getLogger(__name__)


class DatasourceUnavailableError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(f"docker unavailable: {detail}")


class DockerReadOnlyClient:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                self._client = docker.DockerClient(base_url=settings.docker_socket)
            except DockerException as exc:
                raise DatasourceUnavailableError(str(exc)) from exc
        return self._client

    def container_info(self, container_name: str) -> dict:
        """State, health, restart count, image — never docker exec/restart/
        stop (#22)."""
        try:
            client = self._get_client()
            container = client.containers.get(container_name)
            attrs = container.attrs
            state = attrs.get("State", {})
            return {
                "name": container_name,
                "status": state.get("Status"),
                "running": state.get("Running"),
                "restart_count": attrs.get("RestartCount"),
                "started_at": state.get("StartedAt"),
                "health": (state.get("Health") or {}).get("Status"),
                "image": attrs.get("Config", {}).get("Image"),
                "exit_code": state.get("ExitCode"),
                "oom_killed": state.get("OOMKilled"),
            }
        except DockerException as exc:
            raise DatasourceUnavailableError(str(exc)) from exc

    def all_known_containers(self) -> dict[str, dict]:
        result = {}
        for name in settings.known_containers:
            try:
                result[name] = self.container_info(name)
            except DatasourceUnavailableError as exc:
                result[name] = {"error": str(exc)}
        return result
