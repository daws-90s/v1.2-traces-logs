"""Fallback for any alert without a dedicated playbook (e.g. InstanceDown,
HostCPUHigh, HostMemoryHigh, BackendLatencyP95High — all real alerts in
this stack's alert-rules.yml/slo-burn-rate-alerts.yml). Still does a real,
alert-context-aware investigation per #29's "approximate order" rather than
querying everything indiscriminately (#14)."""
from app.investigation.context import InvestigationContext
from app.investigation.evidence import EvidenceBundle
from app.investigation.hypotheses import Hypothesis
from app.loki import queries as loki_q
from app.playbooks.base import safe
from app.prometheus import queries as prom_q
from app.prometheus.models import single_value


class DefaultPlaybook:
    name = "default"

    def run(self, ctx: InvestigationContext) -> EvidenceBundle:
        bundle = EvidenceBundle()
        w = ctx.immediate_window()

        job = ctx.labels.get("job")
        instance = ctx.labels.get("instance")
        target_up = safe(ctx, bundle, "prometheus", "target_up",
                          lambda: ctx.prometheus.target_health(job=job) if job else ctx.prometheus.target_health())
        if target_up:
            for t in target_up[:10]:
                bundle.add("prometheus", f"target job={t.get('labels', {}).get('job')} instance={t.get('scrapeUrl')} health={t.get('health')}")

        cpu = safe(ctx, bundle, "prometheus", "host_cpu", lambda: single_value(ctx.prometheus.instant_query(prom_q.host_cpu_busy_pct())))
        mem = safe(ctx, bundle, "prometheus", "host_mem", lambda: single_value(ctx.prometheus.instant_query(prom_q.host_memory_used_pct())))
        if cpu is not None:
            bundle.add("prometheus", f"host CPU busy = {cpu:.1f}%")
        if mem is not None:
            bundle.add("prometheus", f"host memory used = {mem:.1f}%")

        containers = safe(ctx, bundle, "docker", "known_containers", lambda: ctx.docker.all_known_containers())
        if containers:
            for name, info in containers.items():
                if "error" in info:
                    continue
                bundle.add("docker", f"{name} container status={info.get('status')} restart_count={info.get('restart_count')} health={info.get('health')}")

        service_label = "backend" if job in (None, "expense-backend") else job
        errs = safe(ctx, bundle, "loki", "recent_errors",
                     lambda: ctx.loki.query_range(loki_q.by_service(service_label, "error"), w.start_ns, w.end_ns))
        if errs:
            for entry in errs[-10:]:
                bundle.add("loki", f"{service_label} log: {entry['line'][:300]}", observed_at=entry["timestamp"])

        h_infra = Hypothesis("Host/infrastructure resource pressure")
        if cpu is not None and cpu > 80:
            h_infra.add_supporting(f"prometheus: host CPU busy {cpu:.1f}%")
        if mem is not None and mem > 70:
            h_infra.add_supporting(f"prometheus: host memory used {mem:.1f}%")
        if target_up and any(t.get("health") == "down" for t in target_up):
            h_infra.add_supporting("prometheus: at least one scrape target is down")

        h_app = Hypothesis(f"Application-level issue in {service_label}")
        if errs:
            h_app.add_supporting(f"loki: {len(errs)} error log lines from {service_label} in the incident window")

        return bundle, [h_infra, h_app]
