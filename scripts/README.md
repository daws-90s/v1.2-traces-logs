# Load scripts

`healthy-load.sh` and `fault-load.sh` are plain bash + curl (no k6, no jq)
and talk to the app only through nginx on port 80 (`$BASE_URL/api/...`),
the same path a real browser uses — never the backend's `:4000` directly.
`inject.sh` is a different shape entirely — see its own section below.

## `healthy-load.sh`
Signs up a new random user, then creates a few expenses against one of the
app's default categories (read via `GET /categories`, not created). This is
what "normal traffic" looks like on the RED-metrics and business-metrics
panels — request rate ticking up, `users_registered_total` and
`expenses_created_total` climbing, error rate flat at zero.

```bash
BASE_URL=http://localhost USERS=20 EXPENSES_PER_USER=5 ./healthy-load.sh

# or leave it running continuously while you look at the dashboard:
DURATION_SECONDS=300 ./healthy-load.sh
```

## `fault-load.sh`
Sends genuinely invalid requests — wrong password, duplicate signup, weak
password, no auth cookie, unknown route, malformed JSON — to put real 4xx
and 5xx traffic on the error-rate panel. Every fault here is a bad request
a real client could send; nothing server-side is flipped to produce it.

```bash
BASE_URL=http://localhost ITERATIONS=50 ./fault-load.sh
```

Run both at once (different terminals) to see the 5xx-error-rate panel
move while the request-rate panel keeps climbing from the healthy traffic
underneath it — that contrast is the point.

## `inject.sh`
Different from the two above in one important way: it doesn't send HTTP
traffic at all, it flips flags. `ENABLE_DEBUG_ROUTES=true` on the backend
(see `docker-compose.yml`) exposes a `/debug` API wrapping fault-injection
code that's been sitting unused in the app since `v1.1` — a real N+1 query
path, a real event-loop-blocking busy loop, a real live MySQL
connection-pool resize. Run this *while* `healthy-load.sh` is running
underneath it, so there's real traffic for the fault to actually degrade:

```bash
./healthy-load.sh &                  # real traffic running underneath
./inject.sh n-plus-one on            # now /expenses gets genuinely slower
./inject.sh pool-size 2              # and MySQL connections start queuing

# ... watch it show up in the metrics/traces/logs dashboards ...

./inject.sh stop                     # turn everything back off
```

`/debug/*` is deliberately not reachable through nginx/`$BASE_URL` — see
the script's own header comment for why, and how it gets to the backend
instead.
