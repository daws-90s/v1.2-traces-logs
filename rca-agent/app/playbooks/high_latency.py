"""HighAPIp99Latency playbook (#16). Walks the same
Prometheus -> Tempo -> Loki -> MySQL -> Container chain the spec's own
worked example does, comparing baseline vs current rather than reporting
current values in isolation (#13).
"""
from app.investigation.context import InvestigationContext
from app.investigation.correlation import extract_trace_ids, percent_change
from app.investigation.evidence import EvidenceBundle
from app.investigation.hypotheses import Hypothesis
from app.loki import queries as loki_q
from app.playbooks.base import safe
from app.prometheus import queries as prom_q
from app.prometheus.models import single_value
from app.tempo import queries as tempo_q


class HighLatencyPlaybook:
    name = "HighAPIp99Latency"

    def run(self, ctx: InvestigationContext) -> EvidenceBundle:
        bundle = EvidenceBundle()
        route = ctx.labels.get("route")

        baseline_w, immediate_w = ctx.baseline_window(), ctx.immediate_window()

        p99_baseline = safe(ctx, bundle, "prometheus", "p99_baseline",
                             lambda: single_value(ctx.prometheus.instant_query(
                                 prom_q.http_latency_quantile(0.99, route=route), time_=baseline_w.end_rfc3339)))
        p99_current = safe(ctx, bundle, "prometheus", "p99_current",
                            lambda: single_value(ctx.prometheus.instant_query(
                                prom_q.http_latency_quantile(0.99, route=route))))
        if p99_baseline is not None and p99_current is not None:
            change = percent_change(p99_baseline, p99_current)
            change_str = f"{change:+.0f}%" if change is not None else "n/a"
            bundle.add(
                "prometheus",
                f"backend p99 latency{f' for {route}' if route else ''}: "
                f"baseline={p99_baseline * 1000:.0f}ms current={p99_current * 1000:.0f}ms ({change_str})",
            )

        req_rate = safe(ctx, bundle, "prometheus", "req_rate",
                         lambda: single_value(ctx.prometheus.instant_query(prom_q.http_request_rate())))
        err_rate = safe(ctx, bundle, "prometheus", "err_rate",
                         lambda: single_value(ctx.prometheus.instant_query(prom_q.http_error_rate_5xx())))
        if req_rate is not None:
            bundle.add("prometheus", f"backend request rate = {req_rate:.2f} req/s (request rate itself, to rule out a traffic spike)")
        if err_rate is not None:
            bundle.add("prometheus", f"backend 5xx error rate = {err_rate:.2%}")

        cpu = safe(ctx, bundle, "prometheus", "backend_cpu",
                   lambda: single_value(ctx.prometheus.instant_query(prom_q.container_cpu_usage("backend"))))
        mem = safe(ctx, bundle, "prometheus", "backend_mem",
                   lambda: single_value(ctx.prometheus.instant_query(prom_q.container_memory_usage("backend"))))
        if cpu is not None:
            bundle.add("prometheus", f"backend container CPU usage = {cpu:.2f} cores")
        if mem is not None:
            bundle.add("prometheus", f"backend container memory usage = {mem / 1e6:.0f}MB")

        mysql_util = safe(ctx, bundle, "prometheus", "mysql_conn_util",
                           lambda: single_value(ctx.prometheus.instant_query(prom_q.mysql_connection_utilization())))
        mysql_slow = safe(ctx, bundle, "prometheus", "mysql_slow_queries",
                           lambda: single_value(ctx.prometheus.instant_query(prom_q.mysql_slow_queries_rate())))
        if mysql_util is not None:
            bundle.add("prometheus", f"MySQL connection utilization = {mysql_util:.0%} of max_connections")
        if mysql_slow is not None:
            bundle.add("prometheus", f"MySQL slow_queries rate = {mysql_slow:.3f}/s")

        # --- Tempo: where is the request actually spending its time? ---
        traceql = tempo_q.slow_traces_for_route(route, 1000) if route else tempo_q.slow_traces_for_service(1000)
        slow_traces = safe(ctx, bundle, "tempo", "slow_traces",
                            lambda: ctx.tempo.search(traceql, immediate_w.start_s, immediate_w.end_s))
        db_dominant_count = 0
        sample_trace_id = None
        if slow_traces:
            for t in slow_traces[:10]:
                sample_trace_id = sample_trace_id or t.get("traceID")
                duration_ms = t.get("durationMs", 0)
                bundle.add(
                    "tempo",
                    f"slow trace {t.get('traceID')} rootServiceName={t.get('rootServiceName')} "
                    f"duration={duration_ms}ms",
                )
            detail = safe(ctx, bundle, "tempo", "trace_detail", lambda: ctx.tempo.get_trace(sample_trace_id)) if sample_trace_id else None
            if detail:
                db_ms, total_ms = _sum_db_span_duration(detail)
                if total_ms:
                    db_dominant_count = 1 if db_ms / total_ms > 0.5 else 0
                    bundle.add(
                        "tempo",
                        f"trace {sample_trace_id}: total={total_ms:.0f}ms, mysql/db child-span time={db_ms:.0f}ms "
                        f"({(db_ms / total_ms) if total_ms else 0:.0%} of request duration)",
                    )
        else:
            bundle.add_gap("Tempo returned no slow traces for this window — trace sampling/routing may need review.")

        # --- Loki: correlate the same trace with logs ---
        if sample_trace_id:
            log_hits = safe(ctx, bundle, "loki", "trace_correlated_logs",
                             lambda: ctx.loki.query_range(loki_q.trace_id_search(sample_trace_id), immediate_w.start_ns, immediate_w.end_ns))
            if log_hits:
                for entry in log_hits[:10]:
                    bundle.add("loki", f"log line for trace {sample_trace_id}: {entry['line'][:300]}", observed_at=entry["timestamp"])

        conn_errors = safe(ctx, bundle, "loki", "connection_timeout_logs",
                            lambda: ctx.loki.query_range(loki_q.mysql_connection_errors(), immediate_w.start_ns, immediate_w.end_ns))
        if conn_errors:
            for entry in conn_errors[-10:]:
                bundle.add("loki", f"backend log: {entry['line'][:300]}", observed_at=entry["timestamp"])
            trace_ids_in_logs = extract_trace_ids(conn_errors)
            if trace_ids_in_logs:
                bundle.add("loki", f"connection-error log lines correlate to {len(trace_ids_in_logs)} distinct trace_id(s)")

        # --- Source code: does the implicated route actually run a query
        # shaped like what Tempo/logs show? (#24) ---
        code = safe(ctx, bundle, "github", "expenses_route_source", lambda: ctx.github.get_known_file("backend_expenses_route"))
        if code:
            path, _content = code
            bundle.add("github", f"backend route source available at {path} for manual/LLM correlation with the trace's DB span")

        # --- Hypotheses ---
        h_db = Hypothesis("Database (MySQL) is the bottleneck — connection saturation or slow queries")
        h_cpu = Hypothesis("Backend container resource exhaustion (CPU/memory)")
        h_traffic = Hypothesis("Plain traffic spike, no backend degradation")

        if db_dominant_count:
            h_db.add_supporting(f"tempo: database child spans dominate slow trace {sample_trace_id}'s duration")
        if mysql_util is not None and mysql_util > 0.7:
            h_db.add_supporting(f"prometheus: MySQL connection utilization elevated ({mysql_util:.0%})")
        if mysql_slow is not None and mysql_slow > 0:
            h_db.add_supporting(f"prometheus: MySQL slow_queries actively incrementing ({mysql_slow:.3f}/s)")
        if conn_errors:
            h_db.add_supporting(f"loki: {len(conn_errors)} MySQL connection-error log lines in the incident window")
        if cpu is not None and cpu < 0.8:
            h_db.add_contradicting(f"prometheus: backend CPU usage normal ({cpu:.2f} cores)")

        if cpu is not None and cpu > 0.8:
            h_cpu.add_supporting(f"prometheus: backend container CPU usage high ({cpu:.2f} cores)")
        if mem is not None and mem > 400_000_000:
            h_cpu.add_supporting(f"prometheus: backend container memory usage elevated ({mem / 1e6:.0f}MB)")

        if req_rate is not None and req_rate > 0 and not db_dominant_count and (mysql_util is None or mysql_util < 0.5):
            h_traffic.add_supporting(f"prometheus: request rate {req_rate:.2f}/s with no dominant DB or CPU signal")
        if db_dominant_count or (mysql_util is not None and mysql_util > 0.7):
            h_traffic.add_contradicting("prometheus/tempo: a specific backend bottleneck (DB) was identified")

        return bundle, [h_db, h_cpu, h_traffic]


def _sum_db_span_duration(trace_detail: dict) -> tuple[float, float]:
    """Tempo's /api/traces/{id} response nests spans under
    batches[].scopeSpans[].spans[] (OTLP JSON shape). Sums any span whose
    name/attributes look like a mysql2 auto-instrumentation span against
    the trace's total (root span) duration, both in milliseconds."""
    db_ns, total_ns = 0, 0
    batches = trace_detail.get("batches", []) or trace_detail.get("resourceSpans", [])
    for batch in batches:
        for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])):
            for span in scope.get("spans", []):
                start = int(span.get("startTimeUnixNano", 0))
                end = int(span.get("endTimeUnixNano", 0))
                duration = max(end - start, 0)
                name = (span.get("name") or "").lower()
                is_db = "mysql" in name or any(
                    a.get("key") == "db.system" for a in span.get("attributes", [])
                )
                if span.get("parentSpanId") in (None, "", "0000000000000000"):
                    total_ns = max(total_ns, duration)
                if is_db:
                    db_ns += duration
    return db_ns / 1e6, (total_ns / 1e6) or (db_ns / 1e6)
