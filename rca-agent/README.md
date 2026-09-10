# RCA Agent

An investigation-only autonomous agent that watches this stack's own
Alertmanager for firing alerts, investigates read-only across Prometheus,
Loki, Tempo, MySQL, Docker, and the app's GitHub repos, and posts a
root-cause analysis to Slack. Built against
[`agent-spec.md`](../agent-spec.md) — read that file for the full
requirements; this README is setup + a map of how the code implements it.

It **never modifies** anything — not the app, not the database, not
Docker, not Prometheus/Grafana/Alertmanager/Loki/Tempo config. The only
write operation anywhere in this codebase is posting to Slack. See
[Security model](#security-model) below for how that's enforced in code,
not just by convention.

## What's implemented in this pass

Core pipeline (spec phases 1-6): Alertmanager webhook ingestion + dedup,
Prometheus/Loki/Tempo investigation, MySQL read-only diagnostics, Docker
read-only container health, a hypothesis-driven RCA engine backed by
Claude, threaded Slack reporting, and the security/permission/
sanitization layer. Three playbooks ship (`MySQLDown`, `HighAPIp99Latency`
/ `BackendLatencyP95High`, `HTTP500High` / `Backend5xxRateHigh`), plus a
generic fallback for every other alert in this stack's `alert-rules.yml`.

GitHub source-code correlation (#23-24) and Grafana deep-links (#20) are
wired in — lightweight but functional, not stubs. A fuller Grafana
dashboard-discovery API and a broader automated test suite are the
natural fast-follow (spec phases 7-9) and aren't attempted here.

## Setup

### 1. MySQL read-only user

`expense-mysql-v1/migrations/005_rca_readonly_user.sql` creates a
`rca_agent` user with `SELECT`/`PROCESS` only (same shape as the existing
`metrics_exporter` user). Like every migration in that image, it only runs
on a **fresh** MySQL data volume. If `expense_mysql_data` already exists
from an earlier run:

```bash
docker compose exec mysql mysql -uroot -prootpass -e "$(cat ../expense-mysql-v1/migrations/005_rca_readonly_user.sql)"
```

or `docker compose down -v && docker compose up -d --build` for a clean
volume (loses all existing app data — fine for a teaching stack, not for
anything else).

### 2. Slack bot token

Different mechanism from `../alertmanager/secrets/slack_webhook_url`
(that's an incoming webhook — this needs `chat.postMessage` +
`thread_ts` for threading, which an incoming webhook can't do):

1. https://api.slack.com/apps → **Create New App** → From scratch
2. **OAuth & Permissions** → under **Scopes → Bot Token Scopes**, add
   `chat:write`
3. **Install to Workspace**, approve
4. Copy the **Bot User OAuth Token** (`xoxb-...`)
5. Invite the bot to your target channel (`/invite @your-bot-name`), then
   get that channel's ID (right-click the channel → **View channel
   details** → bottom of the panel)

### 3. Anthropic API key

Create a key at https://console.anthropic.com — this is separate from any
Claude Code subscription; the agent calls the Messages API directly.

### 4. `.env`

```bash
cp .env.example .env
# fill in MYSQL_RCA_PASSWORD (must match 005_rca_readonly_user.sql, or
# change both), SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, LLM_API_KEY
```

### 5. Run it

```bash
cd ..   # v1.2-traces-logs/
docker compose up -d --build rca-agent
```

`docker compose up -d --build` (no service name) brings the whole stack
up including this one, same as always.

## Trying it

Same fault-injection tooling the rest of this stage already uses
(`../scripts/README.md`) produces alerts a real investigation can chew on:

```bash
./scripts/healthy-load.sh
./scripts/inject.sh timeout on    # or busy-loop, n-plus-one
./scripts/fault-load.sh
```

Watch `#your-configured-channel` in Slack: an investigation-started
message appears within seconds of the alert firing, followed by the RCA
summary and detailed report threaded underneath once the investigation
completes (or a "RCA Investigation Incomplete" notice if it hits
`RCA_MAX_INVESTIGATION_SECONDS` first).

To stop MySQL outright for the `MySQLDown` playbook: `docker compose stop
mysql`, then `docker compose start mysql` once you've seen the
investigation (and its `resolved` follow-up) come through.

## Manual trigger (`POST /investigate`)

The agent normally only starts an investigation when Alertmanager posts to
`/webhook/alertmanager` — which means actually breaching a real threshold
first. `/investigate` runs the exact same pipeline on demand, with alert
labels you supply, so you can demo a specific playbook without waiting for
(or faking) a real incident:

```bash
# MySQLDown playbook
curl -s -X POST http://localhost:8080/investigate \
  -H 'Content-Type: application/json' \
  -d '{"alert_name": "MySQLDown", "labels": {"severity": "critical"}}'

# HighAPIp99Latency playbook, scoped to one route
curl -s -X POST http://localhost:8080/investigate \
  -H 'Content-Type: application/json' \
  -d '{"alert_name": "HighAPIp99Latency", "labels": {"route": "/expenses", "severity": "warning"}}'

# HTTP500High playbook
curl -s -X POST http://localhost:8080/investigate \
  -H 'Content-Type: application/json' \
  -d '{"alert_name": "HTTP500High", "labels": {"severity": "warning"}}'
```

Each call returns immediately with an `incident_id` and `fingerprint` —
the investigation itself runs in the background; watch Slack (or `docker
compose logs -f rca-agent`) for the result. Calling it again with the same
`fingerprint` exercises dedup (#30) instead of starting a second
investigation for what's still the same incident. To see the `resolved`
Slack message (#31), call it again with that same fingerprint and
`"status": "resolved"`.

Any `alert_name` works — one of the three playbooks
(`MySQLDown`/`HighAPIp99Latency`/`BackendLatencyP95High`/`HTTP500High`/
`Backend5xxRateHigh`) if it matches, `DefaultPlaybook` otherwise, same as
a real unrecognized Alertmanager alertname would get.

This endpoint has no auth by default — fine on `localhost`/the Docker
network, not fine if port 8080 ends up reachable from the internet (it's
published to the host in `docker-compose.yml`). Set
`RCA_MANUAL_TRIGGER_TOKEN` in `.env` to require an `X-RCA-Trigger-Token`
header matching it if your EC2 security group exposes 8080 (this also
gates `/ask` below).

## Ask a question directly (`POST /ask`)

`/investigate` still needs you to know which playbook/labels to pass.
`/ask` takes a plain question, picks a playbook by keyword match (deliberately
not an LLM call — see `app/investigation/ask.py`'s own comment on why),
and — unlike every other entrypoint — **blocks and returns the answer
directly in the HTTP response** instead of only posting to Slack:

```bash
curl -s -X POST http://localhost:8080/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "investigate a recent high latency request and tell me the reason"}' | jq
```

"latency"/"slow"/"p99"/"timeout" routes to `HighAPIp99Latency` and, since
no specific route was named, the agent first queries Prometheus for
whichever route currently has the worst p99 and investigates that one.
"mysql"/"database down" routes to `MySQLDown`, "500"/"errors"/"failing" to
`HTTP500High`; anything else falls through to the generic playbook (host/
container/log sweep). The Slack investigation-started/RCA messages still
go out as usual if Slack is configured — `/ask` is an additional way to
get the answer, not a replacement for the Slack flow.

Defaults to a 60s budget (`RCA_ASK_MAX_SECONDS`, capped at
`RCA_MAX_INVESTIGATION_SECONDS`) since you're waiting on the HTTP
response — override per-call with `"timeout_seconds": 120` in the request
body if a slower playbook needs more room. A timeout returns a 200 with
`"confidence": "Unknown"` and a note to check Slack/logs, not an error.

## Architecture

```
Alertmanager --webhook--> POST /webhook/alertmanager (app/main.py)
                                  |
                                  v
                    app/investigation/orchestrator.py
                          (dedup, lifecycle, timeout)
                                  |
                    app/playbooks/registry.py picks a playbook
                    by alertname (mysql_down / high_latency / http_500 /
                    default), which gathers app/investigation/evidence.py
                    Evidence from the read-only clients:
                    prometheus/ loki/ tempo/ mysql/ docker/ github/
                                  |
                    app/rca/engine.py: deterministic hypothesis ranking
                    + confidence (app/investigation/{hypotheses,confidence}.py)
                    -> app/llm/client.py (Anthropic) writes the narrative
                    -> final confidence = min(deterministic, LLM's own)
                                  |
                    app/rca/{summary,report}.py format Slack text/markdown
                                  |
                    app/slack/client.py posts into the incident's thread
```

Every module maps to a section of `agent-spec.md` — most files carry a
docstring pointing at the specific section(s) they implement.

## Security model

- **No mutation surface exists**: there is no `execute_shell()`, no
  arbitrary-SQL executor, no Docker exec/restart/stop call, no GitHub
  write call, anywhere in this codebase — not gated behind a flag, simply
  absent. `app/security/permissions.py` documents (and tests) this
  explicitly.
- **MySQL**: `app/mysql/readonly_client.py` can only run a query by a
  fixed key into `SAFE_QUERY_LIBRARY` — there is no method that accepts
  SQL text from a caller, LLM included. The `rca_agent` database account
  itself also can't write anything, so this is defense in depth, not the
  only layer.
- **Docker**: read-only socket mount (same as `alloy`'s existing mount),
  and `app/docker/readonly_client.py` only ever calls
  `list`/`get`/`.attrs` — never `.exec_run()`/`.restart()`/`.stop()`.
- **The LLM has no tools.** `app/llm/client.py` sends evidence as text and
  parses a JSON response — there is no function-calling loop, so there is
  nothing for a compromised LLM to invoke even in principle (#70). Prompt
  injection from logs/traces/source code is handled by treating that
  content as fenced, explicitly-untrusted data
  (`app/security/sanitization.py`'s `wrap_untrusted`), not by asking the
  model nicely.
- **Slack is the only write.** `app/slack/client.py` is the sole class in
  this codebase with a `POST`/write call other than the two read-only
  API clients' own GET requests.
- **Secrets** never reach the LLM prompt or a Slack message —
  `app/security/sanitization.py` redacts tokens/passwords/JWTs/cookies/
  emails from evidence before it's used anywhere downstream.

## What's deliberately out of scope here

- **Remediation.** `expense-backend-v1.2/src/routes/agentRemediation.js`
  (the allow-listed container-restart endpoint,
  `ENABLE_AGENT_REMEDIATION`) is a later stage's concern, not this one's
  — see that file and the backend's own `.env.example`. This agent never
  calls it.
- **A full Grafana dashboard/datasource discovery API** — `app/grafana/
  client.py` generates Explore/dashboard links (useful, real), not a
  crawler of Grafana's own API.
- **A broader test suite** (rate limiting, prompt-injection end-to-end,
  full failure-mode matrix) — `tests/` covers the permission boundary,
  sanitization, confidence scoring, and dedup; expanding it is natural
  fast-follow work.
