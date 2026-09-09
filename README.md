# Traces & Logs (OpenTelemetry + Tempo, Grafana Loki + Alloy)

Second stage: metrics+alerting → **traces + logs**. Everything from
`v1.1-metrics` stays running — Prometheus, Alertmanager, the two
dashboards — this stage adds distributed tracing and log aggregation on
top of it in one shot, rather than as two separate stages, and finishes
the correlation between all three signals that the app's own code has
been quietly built for since `v1.1`.

## What's new vs. v1.1-metrics

- **`expense-backend-v1.2` / `expense-frontend-v1.2`** — new forks, so a
  student on the metrics stage never has a later stage's history in front
  of them. The real changes are tracing-related (see below); logging rides
  along for free once tracing is on.
- **nginx (`ngx_otel_module`)** — starts a trace on every `/api/` request
  and propagates it to the backend. `location /` (static assets) stays
  untraced on purpose.
- **OTel Collector** — the single ingestion point every trace flows
  through (backend via OTLP/HTTP, nginx via OTLP/gRPC) before being
  forwarded to Tempo.
- **Tempo** — stores traces, keyed by `trace_id`.
- **Loki** — stores logs.
- **Grafana Alloy** — reads every container's stdout off the Docker socket
  and ships it to Loki. Nothing in the app images changes to make this
  work; Alloy just tails whatever they were already writing.
- **Grafana**: `Tempo` and `Loki` datasources, plus a `Prometheus`
  exemplar destination — all three signals cross-linked in both
  directions (see "The bridges" below).

## What actually changed in the backend, and why logging came free

`expense-backend-v1.1`'s `pino`/`pino-http` setup
(`src/middleware/requestLogger.js`) has shipped structured JSON logs with
`route`, `user_id`, `duration_ms`, and a level derived from `status_code`
since the metrics stage — that didn't need to change at all.

What's new at `v1.2` is entirely in service of *tracing*:
`src/tracing.js` starts an OTel Node SDK with
`getNodeAutoInstrumentations()`, which patches `http`/`express`/`mysql2`
so every request and query gets a span with zero other code changes. A
manual span was also added around `bcrypt.hash`/`bcrypt.compare` in
`auth.js` — bcrypt isn't a library OTel knows how to instrument on its
own, and without it the app's slowest routes would show up as an
unexplained gap in their own trace.

The logging payoff is a side effect of that same change, not separate
work: `getNodeAutoInstrumentations()` also bundles
`@opentelemetry/instrumentation-pino`, which injects `trace_id`/`span_id`
into every log line the moment a real OTel SDK is running — it was inert
before `v1.2` for the simple reason that no SDK existed yet to report a
trace from. Turning on tracing is what turns on log correlation too.

## What's running

```bash
cd v1.2-traces-logs
docker compose up -d --build
```

Same ports as before: app on `:80`, Prometheus on `:9090`, Alertmanager on
`:9093`, Grafana on `:3000` (`admin`/`admin`). No new host ports —
`otel-collector`, `tempo`, `loki`, and `alloy` are only reachable from
other containers on this stack's network.

- **`otel-collector`** (`otel/opentelemetry-collector:0.160.0`) — batches
  and forwards every span to Tempo.
- **`tempo`** (`grafana/tempo:3.0.3`) — single-binary mode, local
  filesystem storage, no named volume (a teaching stack's trace history
  isn't meant to survive a container recreation, and a fresh
  `--force-recreate` gives every class a clean start — same reasoning
  `prometheus` already uses).
- **`loki`** (`grafana/loki:3.7.7`) — same no-named-volume reasoning as
  `tempo`.
- **`alloy`** (`grafana/alloy:v1.19.2`) — `alloy/config.alloy` does three
  things: `discovery.docker` finds every running container,
  `discovery.relabel` turns Docker's container name/compose-service
  metadata into real Loki labels, `loki.source.docker` tails each
  container's log stream and forwards it to `loki.write`, which pushes it
  to Loki. Mounts `/var/run/docker.sock` read-only — it only ever reads
  container metadata and log output through it.
- Prometheus gets four new scrape jobs — `otel-collector`, `tempo`,
  `loki`, `alloy` (all expose `/metrics` on their main port) — same
  self-monitoring pattern as every other job, so `InstanceDown` already
  covers any of them going down. No new alert rule needed.

## The bridges, in both directions

- **Metrics → trace.** Every `http_request_duration_seconds` observation
  carries a real `trace_id` as a Prometheus exemplar
  (`--enable-feature=exemplar-storage`); the little diamond marks on the
  p50/p95/p99 panels jump straight into Tempo.
- **Logs → trace.** The Loki datasource's `derivedFields` regex-matches
  `"trace_id":"([0-9a-f]+)"` against a log line's raw JSON text and turns
  it into a **View Trace** link straight into Tempo — the same `trace_id`
  field `instrumentation-pino` now injects, finally given somewhere to go.
- **Trace → logs.** The Tempo datasource's `tracesToLogsV2` does the
  reverse: from any span, jump to its logs in Loki. Deliberately **not**
  done by labeling every log line with its own `trace_id` — a Loki label
  needs low, bounded cardinality, and a fresh value on every request is
  exactly the shape of label that makes Loki's index fall over at real
  volume. `filterByTraceID: true` instead runs a plain `|="<traceID>"`
  text search, the LogQL equivalent of grepping — slower per query than a
  label lookup, correct at any scale.

## Trying it

```bash
./scripts/healthy-load.sh
./scripts/fault-load.sh
```

Then, in Grafana:
1. **Explore → Tempo**, TraceQL search or the Search tab — find a real
   `/api/` trace and open it. The `bcrypt.hash`/`bcrypt.compare` span is
   the one manual span in this whole app.
2. **Explore → Loki.** Try `{compose_service="backend"}` — every log line
   from the backend container, structured JSON. Add `| json` to parse it
   into fields you can filter on, e.g. `{compose_service="backend"} |
   json | status_code >= 500`.
3. Click any line's **View Trace** button (only appears on lines that
   actually have a `trace_id`, e.g. requests through `/api/`) — jumps
   straight into that request's trace in Tempo.
4. From a trace in Tempo, use **Logs for this span** to jump back the
   other way.
5. `{compose_service="frontend"}` shows nginx's own access log lines —
   only `/api/` requests carry a real `trace_id`; `location /` (static
   assets) is deliberately untraced.

## Making something worth investigating

`healthy-load.sh`/`fault-load.sh` only produce traffic a real client could
send. For genuine server-side degradation — a slow query, MySQL under real
connection pressure, a busy backend — `ENABLE_DEBUG_ROUTES=true` (set on
the backend above) turns on a `/debug` API that's been sitting unused in
the app since `v1.1`. `scripts/inject.sh` drives it; see
[`scripts/README.md`](scripts/README.md#injectsh) for the full rundown.
Run it alongside `healthy-load.sh` and watch all three signals — metrics,
traces, logs — move for the same real reason at once.

## What's still deliberately missing

No log-based alerting (Loki's `ruler` component isn't configured) and no
dedicated logs dashboard panel — Explore is the right tool while you're
still learning LogQL and TraceQL by hand. Both are reasonable next steps
once this stage's mechanics are familiar, not missing by oversight.

## RCA agent

`alertmanager.yml`'s `webhook_configs` entry also forwards every firing/
resolved alert to `rca-agent`, an investigation-only Python service that
correlates the three signals above (plus MySQL, Docker, and the app's
GitHub source) into a hypothesis-driven root-cause analysis, posted to a
Slack thread. See [`rca-agent/README.md`](rca-agent/README.md) for setup
— it needs its own Slack bot token and an Anthropic API key, neither of
which this stage's `docker compose up` provides by default — and
[`agent-spec.md`](agent-spec.md) for the full design it's built against.
