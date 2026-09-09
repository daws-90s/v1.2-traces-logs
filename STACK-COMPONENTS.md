# What's in this stack

Every service in `docker-compose.yml`, grouped by what it's actually for.
14 containers, four jobs: run the app, collect the three signals
(metrics/traces/logs), store each signal, and turn all of it into
something a human gets paged about or looks at in Grafana.

## The app itself

| Service | What it is |
|---|---|
| **mysql** | The database (`expense-mysql-v1`) — users, expenses, categories. Unmodified across every stage; no schema change has been needed yet. |
| **backend** | The API (`expense-backend-v1.2`, Node/Express). Emits all three signals itself: `/metrics` for Prometheus, OTLP traces via auto-instrumentation, and structured JSON logs (pino) to stdout. |
| **frontend** | nginx (`expense-frontend-v1.2`) — serves the built React app and reverse-proxies `/api/` to the backend. Also where request tracing actually starts (`ngx_otel_module`) and where nginx's own request-duration histograms come from (`nginx-module-vts`). |

## Metrics: collect, store, alert

| Service | What it is |
|---|---|
| **mysqld-exporter** | Logs into MySQL as a read-only user and re-exposes its internal status variables (connections, slow queries, uptime) as Prometheus metrics. MySQL doesn't speak Prometheus natively — this is the translation layer. |
| **nginx-exporter** | Same idea, for nginx: polls `stub_status` and re-exposes it. |
| **node-exporter** | Host-level metrics — CPU, memory, disk, filesystem — read from `/proc` and `/sys` on the EC2 host itself, not from any container. |
| **cadvisor** | Per-container CPU/memory, reading through containerd's own socket (this host's storage backend needs that path specifically — see the service's own comments in `docker-compose.yml`). |
| **prometheus** | Pulls (scrapes) all of the above on a timer, stores it as time series, and evaluates every rule in `alert-rules.yml`/`slo-rules.yml`/`slo-burn-rate-alerts.yml` against that data. The one component nearly everything else in this stack exists to feed. |
| **alertmanager** | Prometheus decides *what's* true (a rule fired); Alertmanager decides *who hears about it* — routes a firing alert to email and Slack. |

## Traces: collect, store

| Service | What it is |
|---|---|
| **otel-collector** | The single ingestion point for every trace in this stack. The backend and nginx each export spans to it (OTLP/HTTP and OTLP/gRPC respectively); it batches them and forwards everything to Tempo. Nothing exports straight to Tempo — always through here. |
| **tempo** | Stores traces, keyed by `trace_id`. No query language of its own to learn for basic use — Grafana's Tempo UI and TraceQL search cover it. |

## Logs: ship, store

| Service | What it is |
|---|---|
| **alloy** | Reads every container's stdout straight off the Docker socket and ships it to Loki. The app images don't do anything special to make this work — they were already writing structured JSON; Alloy just tails it. |
| **loki** | Stores logs, indexed by a small set of labels (container name, compose service) rather than by full text — the labels narrow down *which* stream to search, LogQL does the actual text/field filtering within it. |

## Visualization

| Service | What it is |
|---|---|
| **grafana** | The one UI over all three stores — Prometheus, Tempo, and Loki are each wired in as a datasource. Dashboards are provisioned from `grafana/dashboards/*.json` on startup, not clicked together by hand. Also where the metrics↔traces↔logs correlation actually shows up: an exemplar on a latency panel jumps into Tempo, a trace's span jumps into its logs, a log line's `trace_id` jumps back into Tempo. |

### Exemplars
Exemplars are individual trace references attached to specific data points in a Prometheus histogram or counter metric. Instead of just recording "500ms took X requests," an exemplar tags that observation with a trace_id, so from a spike on a latency graph in Grafana you can click through directly to the one specific trace that produced it.

## The shape underneath all of it

Every exporter/collector pattern above is the same idea repeated: **something that doesn't speak Prometheus/OTLP/Loki's push API natively gets a small process in front of it that translates.** mysqld-exporter and nginx-exporter do this for metrics; the OTel Collector does it for traces (a buffering/fan-out point, not just a translator); Alloy does it for logs. Once that pattern is visible once, the other two are the same shape, not three separate things to memorize.
