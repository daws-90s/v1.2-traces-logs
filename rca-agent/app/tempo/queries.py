"""TraceQL builders. service.name values ("expense-backend",
"expense-frontend") come from tracing.js / nginx.conf's own OTel resource
attributes — same values alloy's config.alloy re-derives as `service_name`
for the Loki<->Tempo correlation to line up (#19)."""

BACKEND_SERVICE = "expense-backend"
FRONTEND_SERVICE = "expense-frontend"


def slow_traces_for_route(route: str, min_duration_ms: int, service: str = BACKEND_SERVICE) -> str:
    return f'{{ resource.service.name="{service}" && name="{route}" && duration > {min_duration_ms}ms }}'


def slow_traces_for_service(min_duration_ms: int, service: str = BACKEND_SERVICE) -> str:
    return f'{{ resource.service.name="{service}" && duration > {min_duration_ms}ms }}'


def error_traces(service: str = BACKEND_SERVICE) -> str:
    return f'{{ resource.service.name="{service}" && status=error }}'


def traces_for_service(service: str = BACKEND_SERVICE) -> str:
    return f'{{ resource.service.name="{service}" }}'
