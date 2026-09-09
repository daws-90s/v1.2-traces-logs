"""Central configuration, read once from the environment at import time.

Defaults point at this stack's real Docker-internal service names/ports
(docker-compose.yml in the parent directory) rather than guessed values —
see agent-spec.md #68/#69. Nothing here is a secret by itself; the actual
values (Slack bot token, LLM key, MySQL password) come from
rca-agent/.env, which is gitignored.
"""
import os


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val else default


class Settings:
    def __init__(self):
        # --- Observability datasources ---
        self.prometheus_url = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
        self.alertmanager_url = os.environ.get("ALERTMANAGER_URL", "http://alertmanager:9093")
        self.loki_url = os.environ.get("LOKI_URL", "http://loki:3100")
        self.tempo_url = os.environ.get("TEMPO_URL", "http://tempo:3200")
        self.grafana_url = os.environ.get("GRAFANA_URL", "http://grafana:3000")

        # --- MySQL read-only access (see migrations/005_rca_readonly_user.sql
        # in expense-mysql-v1 — same least-privilege pattern as
        # metrics_exporter in 003_metrics_user.sql) ---
        self.mysql_host = os.environ.get("MYSQL_HOST", "mysql")
        self.mysql_port = _int("MYSQL_PORT", 3306)
        self.mysql_database = os.environ.get("MYSQL_DATABASE", "expense_tracker")
        self.mysql_rca_username = os.environ.get("MYSQL_RCA_USERNAME", "rca_agent")
        self.mysql_rca_password = os.environ.get("MYSQL_RCA_PASSWORD", "")

        # --- Docker read-only visibility (same socket alloy already mounts
        # read-only in docker-compose.yml — no new privilege class) ---
        self.docker_socket = os.environ.get("DOCKER_SOCKET", "unix:///var/run/docker.sock")
        self.known_containers = ("mysql", "backend", "frontend")

        # --- Slack: bot-token Web API (chat:write), NOT the incoming
        # webhook alertmanager/secrets/slack_webhook_url uses — this one
        # needs chat.postMessage + thread_ts for threading (#45), which an
        # incoming webhook can't do. See rca-agent/README.md for setup. ---
        self.slack_bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        self.slack_channel_id = os.environ.get("SLACK_CHANNEL_ID", "")

        # --- GitHub: public repos, no auth required for read access ---
        self.github_repos = {
            "backend": os.environ.get("GITHUB_BACKEND_REPO", "daws-90s/expense-backend-v1.2"),
            "frontend": os.environ.get("GITHUB_FRONTEND_REPO", "daws-90s/expense-frontend-v1.2"),
            "mysql": os.environ.get("GITHUB_MYSQL_REPO", "daws-90s/expense-mysql-v1"),
        }
        self.github_token = os.environ.get("GITHUB_TOKEN", "")

        # --- LLM (Anthropic Messages API) ---
        self.llm_provider = os.environ.get("LLM_PROVIDER", "anthropic")
        self.llm_model = os.environ.get("LLM_MODEL", "claude-sonnet-5")
        self.llm_api_key = os.environ.get("LLM_API_KEY", "")

        # --- Guardrails (#47, #49) ---
        self.max_investigation_seconds = _int("RCA_MAX_INVESTIGATION_SECONDS", 300)
        self.max_tool_calls = _int("MAX_TOOL_CALLS_PER_INVESTIGATION", 50)
        self.query_timeout_seconds = _int("QUERY_TIMEOUT_SECONDS", 10)
        self.max_log_lines = _int("MAX_LOG_LINES", 200)
        self.max_trace_results = _int("MAX_TRACE_RESULTS", 50)

        self.state_db_path = os.environ.get("RCA_STATE_DB_PATH", "/data/incidents.db")
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        self.port = _int("PORT", 8080)


settings = Settings()
