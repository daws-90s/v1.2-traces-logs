"""MySQLDown playbook (#15). Distinguishes "MySQL is actually down" from
"the exporter can't reach it while MySQL itself is healthy" — the two
possible root causes the spec calls out by name.
"""
from app.investigation.context import InvestigationContext
from app.investigation.evidence import EvidenceBundle
from app.investigation.hypotheses import Hypothesis
from app.loki import queries as loki_q
from app.playbooks.base import safe
from app.prometheus import queries as prom_q
from app.prometheus.models import single_value


class MySQLDownPlaybook:
    name = "MySQLDown"

    def run(self, ctx: InvestigationContext) -> EvidenceBundle:
        bundle = EvidenceBundle()

        target_up = safe(ctx, bundle, "prometheus", "mysql_target_up",
                          lambda: ctx.prometheus.target_health(job="mysql"))
        mysql_up_val = safe(ctx, bundle, "prometheus", "mysql_up",
                             lambda: single_value(ctx.prometheus.instant_query(prom_q.mysql_up())))
        if target_up:
            for t in target_up:
                health = t.get("health")
                bundle.add(
                    "prometheus",
                    f"prometheus target job=mysql instance={t.get('scrapeUrl')} health={health}"
                    f"{' last_error=' + t.get('lastError', '') if t.get('lastError') else ''}",
                )
        if mysql_up_val is not None:
            bundle.add("prometheus", f"mysql_up gauge = {mysql_up_val} (1=exporter reached MySQL, 0=it could not)")

        container = safe(ctx, bundle, "docker", "mysql_container_info", lambda: ctx.docker.container_info("mysql"))
        if container:
            bundle.add(
                "docker",
                f"mysql container status={container.get('status')} running={container.get('running')} "
                f"restart_count={container.get('restart_count')} health={container.get('health')} "
                f"oom_killed={container.get('oom_killed')}",
                observed_at=container.get("started_at"),
            )

        w = ctx.immediate_window()
        mysql_logs = safe(ctx, bundle, "loki", "mysql_logs",
                           lambda: ctx.loki.query_range(loki_q.mysql_logs(), w.start_ns, w.end_ns))
        if mysql_logs:
            for entry in mysql_logs[-20:]:
                bundle.add("loki", f"mysql log: {entry['line'][:300]}", observed_at=entry["timestamp"])
        elif mysql_logs == []:
            bundle.add_gap("No MySQL container log lines found in the immediate window.")

        backend_conn_errors = safe(
            ctx, bundle, "loki", "backend_mysql_connection_errors",
            lambda: ctx.loki.query_range(loki_q.mysql_connection_errors(), w.start_ns, w.end_ns),
        )
        if backend_conn_errors:
            for entry in backend_conn_errors[-10:]:
                bundle.add("loki", f"backend log: {entry['line'][:300]}", observed_at=entry["timestamp"])

        # Only attempt the MySQL protocol connection itself if the exporter
        # thinks it's reachable — otherwise this would just be a second,
        # slower way of learning the same "unreachable" fact.
        if mysql_up_val == 1:
            diag = safe(ctx, bundle, "mysql", "diagnostics_snapshot", lambda: ctx.mysql.diagnostics_snapshot())
            if diag:
                bundle.add("mysql", f"MySQL diagnostics snapshot (agent connected successfully): {diag}")

        backend_5xx = safe(ctx, bundle, "prometheus", "backend_5xx_rate",
                            lambda: single_value(ctx.prometheus.instant_query(prom_q.http_error_rate_5xx())))
        if backend_5xx is not None:
            bundle.add("prometheus", f"backend 5xx error rate = {backend_5xx:.2%}")

        # --- Hypotheses ---
        h_actually_down = Hypothesis("MySQL container/process is actually down or unreachable")
        h_exporter_only = Hypothesis("mysqld_exporter is unavailable while MySQL itself is healthy")

        if mysql_up_val == 0:
            h_actually_down.add_supporting("prometheus: mysql_up is 0 (exporter reached MySQL and it did not answer, or exporter can't reach it)")
        if container and not container.get("running"):
            h_actually_down.add_supporting(f"docker: mysql container status is {container.get('status')}, not running")
        if container and container.get("running") and mysql_up_val == 0:
            h_exporter_only.add_supporting("docker: mysql container is running, but mysql_up is still 0")
        if container and container.get("restart_count", 0) and container.get("running"):
            h_actually_down.add_supporting(f"docker: mysql container restart_count={container.get('restart_count')}")
        if backend_conn_errors:
            h_actually_down.add_supporting(f"loki: {len(backend_conn_errors)} backend log lines show MySQL connection failures")
        if backend_5xx and backend_5xx > 0.05:
            h_actually_down.add_supporting(f"prometheus: backend 5xx rate elevated ({backend_5xx:.2%})")

        return bundle, [h_actually_down, h_exporter_only]
