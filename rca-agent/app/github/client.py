"""Read-only GitHub source inspection (#23). All three application repos
are public (daws-90s/expense-{frontend,backend}-v1.2, expense-mysql-v1), so
no token is required by default; GITHUB_TOKEN, if set, is only ever used on
GET requests here — nothing in this client can create a commit, comment,
or branch.
"""
import base64
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_RAW_BASE = "https://raw.githubusercontent.com"
_API_BASE = "https://api.github.com"


class DatasourceUnavailableError(Exception):
    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(f"github unavailable: {detail}")


# Known hot files per playbook — kept small and explicit rather than a
# search API integration, since the investigation already knows which
# service/route is implicated by the time it looks at source (#24).
KNOWN_FILES = {
    "backend_expenses_route": ("backend", "src/routes/expenses.js"),
    "backend_auth_route": ("backend", "src/routes/auth.js"),
    "backend_db": ("backend", "src/db.js"),
    "backend_metrics": ("backend", "src/metrics.js"),
    "mysql_schema": ("mysql", "migrations/001_init.sql"),
}


class GitHubClient:
    def __init__(self):
        self._headers = {}
        if settings.github_token:
            self._headers["Authorization"] = f"token {settings.github_token}"

    def get_file(self, service: str, path: str, ref: str = "main") -> str:
        repo = settings.github_repos.get(service)
        if not repo:
            raise DatasourceUnavailableError(f"no repository configured for service {service!r}")
        url = f"{_RAW_BASE}/{repo}/{ref}/{path}"
        try:
            resp = httpx.get(url, headers=self._headers, timeout=settings.query_timeout_seconds)
            if resp.status_code == 404 and ref == "main":
                # Older forks in this course may still default to `master`.
                resp = httpx.get(
                    f"{_RAW_BASE}/{repo}/master/{path}",
                    headers=self._headers,
                    timeout=settings.query_timeout_seconds,
                )
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            raise DatasourceUnavailableError(str(exc)) from exc

    def get_known_file(self, key: str) -> tuple[str, str] | None:
        entry = KNOWN_FILES.get(key)
        if not entry:
            return None
        service, path = entry
        return path, self.get_file(service, path)

    def recent_commits(self, service: str, limit: int = 5) -> list[dict]:
        repo = settings.github_repos.get(service)
        if not repo:
            raise DatasourceUnavailableError(f"no repository configured for service {service!r}")
        try:
            resp = httpx.get(
                f"{_API_BASE}/repos/{repo}/commits",
                params={"per_page": limit},
                headers=self._headers,
                timeout=settings.query_timeout_seconds,
            )
            resp.raise_for_status()
            return [
                {
                    "sha": c["sha"][:7],
                    "message": c["commit"]["message"].splitlines()[0],
                    "date": c["commit"]["author"]["date"],
                }
                for c in resp.json()
            ]
        except httpx.HTTPError as exc:
            raise DatasourceUnavailableError(str(exc)) from exc
