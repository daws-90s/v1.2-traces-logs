"""HTTP500High playbook (#17) / Backend5xxRateHigh in this stack's own
alert-rules.yml. Identifies the affected endpoint, correlates failed
traces with backend error logs, and checks the obvious downstream
dependency (MySQL) before checking source.
"""
from app.investigation.context import InvestigationContext
from app.investigation.evidence import EvidenceBundle
from app.investigation.hypotheses import Hypothesis
from app.loki import queries as loki_q
from app.playbooks.base import safe
from app.prometheus import queries as prom_q
from app.prometheus.models import parse_instant_result, single_value
from app.tempo import queries as tempo_q


class Http500Playbook:
    name = "HTTP500High"

    def run(self, ctx: InvestigationContext) -> EvidenceBundle:
        bundle = EvidenceBundle()
        w = ctx.immediate_window()

        by_route_status = safe(ctx, bundle, "prometheus", "requests_by_route_status",
                                lambda: ctx.prometheus.instant_query(prom_q.http_requests_by_route_status()))
        top_route = None
        if by_route_status:
            samples = parse_instant_result(by_route_status)
            errors = [s for s in samples if str(s.labels.get("status_code", "")).startswith("5")]
            errors.sort(key=lambda s: s.value, reverse=True)
            for s in errors[:5]:
                bundle.add(
                    "prometheus",
                    f"route={s.labels.get('route')} status_code={s.labels.get('status_code')} rate={s.value:.3f}/s",
                )
            if errors:
                top_route = errors[0].labels.get("route")

        err_rate = safe(ctx, bundle, "prometheus", "err_rate_5xx",
                         lambda: single_value(ctx.prometheus.instant_query(prom_q.http_error_rate_5xx())))
        req_rate = safe(ctx, bundle, "prometheus", "req_rate",
                         lambda: single_value(ctx.prometheus.instant_query(prom_q.http_request_rate())))
        if err_rate is not None:
            bundle.add("prometheus", f"overall 5xx rate = {err_rate:.2%}")
        if req_rate is not None:
            bundle.add("prometheus", f"overall request rate = {req_rate:.2f} req/s (rules out '5xx rate high only because volume is near zero')")

        backend_errors = safe(ctx, bundle, "loki", "backend_errors",
                               lambda: ctx.loki.query_range(loki_q.backend_status_5xx(), w.start_ns, w.end_ns))
        if backend_errors:
            for entry in backend_errors[-15:]:
                bundle.add("loki", f"backend 5xx log: {entry['line'][:300]}", observed_at=entry["timestamp"])
        else:
            bundle.add_gap("No structured 5xx log lines found — backend may be returning 5xx without logging the error (check requestLogger/error handler coverage).")

        traceql = tempo_q.error_traces()
        failed_traces = safe(ctx, bundle, "tempo", "failed_traces",
                              lambda: ctx.tempo.search(traceql, w.start_s, w.end_s))
        if failed_traces:
            for t in failed_traces[:10]:
                bundle.add("tempo", f"failed trace {t.get('traceID')} rootServiceName={t.get('rootServiceName')} duration={t.get('durationMs')}ms")

        conn_errors = safe(ctx, bundle, "loki", "mysql_connection_errors",
                            lambda: ctx.loki.query_range(loki_q.mysql_connection_errors(), w.start_ns, w.end_ns))
        if conn_errors:
            for entry in conn_errors[-10:]:
                bundle.add("loki", f"backend log: {entry['line'][:300]}", observed_at=entry["timestamp"])

        mysql_util = safe(ctx, bundle, "prometheus", "mysql_conn_util",
                           lambda: single_value(ctx.prometheus.instant_query(prom_q.mysql_connection_utilization())))
        if mysql_util is not None:
            bundle.add("prometheus", f"MySQL connection utilization = {mysql_util:.0%}")

        code_key = "backend_expenses_route" if (top_route and "expense" in top_route) else "backend_auth_route" if (top_route and "auth" in top_route) else None
        if code_key:
            code = safe(ctx, bundle, "github", "route_source", lambda: ctx.github.get_known_file(code_key))
            if code:
                path, _content = code
                bundle.add("github", f"backend route source available at {path} for the {top_route} endpoint")

        # --- Hypotheses ---
        h_db_dependency = Hypothesis("Downstream MySQL dependency failure/timeout causing 5xx")
        h_app_bug = Hypothesis("Application-level error (unhandled exception / validation) unrelated to MySQL")

        if conn_errors:
            h_db_dependency.add_supporting(f"loki: {len(conn_errors)} MySQL connection-error log lines during the incident window")
        if mysql_util is not None and mysql_util > 0.7:
            h_db_dependency.add_supporting(f"prometheus: MySQL connection utilization elevated ({mysql_util:.0%})")
        if failed_traces:
            h_db_dependency.add_supporting(f"tempo: {len(failed_traces)} failed traces in window, available for span-level inspection")

        if backend_errors and not conn_errors:
            h_app_bug.add_supporting(f"loki: {len(backend_errors)} backend 5xx log lines with no accompanying MySQL connection errors")
        if conn_errors:
            h_app_bug.add_contradicting("loki: MySQL connection errors present in the same window")

        return bundle, [h_db_dependency, h_app_bug]
