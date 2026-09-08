#!/usr/bin/env bash
# inject.sh — flips the backend's own dormant fault-injection flags on and
# off. Unlike healthy-load.sh/fault-load.sh, this never talks to nginx:
# /debug/* is deliberately NOT proxied under /api/ (see
# expense-frontend-v1.2/nginx.conf — only /api/ is), so it's unreachable
# from outside the Docker network by design. Every call here goes through
# `docker compose exec` into the backend container itself, using busybox
# wget (present in the node:alpine base image) rather than curl (not
# installed there, and installing one would be an app-code change this
# script has no business making).
#
# None of routes/debug.js or middleware/debugInjections.js is new code —
# both have been sitting in expense-backend unchanged since v1.1, gated
# behind ENABLE_DEBUG_ROUTES (now on for this stage — see docker-compose.yml).
#
# Usage:
#   ./inject.sh status
#   ./inject.sh n-plus-one on|off     # real N+1 query pattern — genuinely
#                                      # slow expense listing, one child span
#                                      # per expense in the trace
#   ./inject.sh busy-loop on|off      # 20% of requests block the event loop
#                                      # for 200ms — real CPU/event-loop
#                                      # pressure on the backend itself
#   ./inject.sh pool-size <n>         # live-resizes the MySQL connection
#                                      # pool — shrink it under load for
#                                      # genuine connection contention
#   ./inject.sh timeout on|off        # a request is simply never answered;
#                                      # nginx's own proxy_read_timeout
#                                      # eventually returns a real 504
#   ./inject.sh bad-cardinality on|off # a debug-only per-user metric label —
#                                      # a real cardinality problem for
#                                      # Prometheus, not a simulated one
#   ./inject.sh stop                  # turns every flag off, restores the
#                                      # pool to its default size
set -euo pipefail

DC="docker compose exec -T backend"
DEFAULT_POOL_SIZE="${DEFAULT_POOL_SIZE:-10}"

call() {
  local path="$1" data="${2:-}"
  if [ -n "$data" ]; then
    $DC wget -qO- --header='Content-Type: application/json' --post-data="$data" "http://localhost:4000${path}"
  else
    $DC wget -qO- "http://localhost:4000${path}"
  fi
  echo
}

toggle() {
  local flag="$1" state="${2:-}"
  case "$state" in
    on) call "/debug/inject/${flag}" '{"enabled":true}' ;;
    off) call "/debug/inject/${flag}" '{"enabled":false}' ;;
    *) echo "usage: $0 ${flag} on|off" >&2; exit 1 ;;
  esac
}

cmd="${1:-}"
case "$cmd" in
  status)
    call "/debug/status"
    ;;
  n-plus-one|busy-loop|timeout|bad-cardinality)
    toggle "$cmd" "${2:-}"
    ;;
  pool-size)
    size="${2:-}"
    [ -n "$size" ] || { echo "usage: $0 pool-size <n>" >&2; exit 1; }
    call "/debug/pool/resize" "{\"size\":${size}}"
    ;;
  stop)
    echo "[inject] turning every flag off, restoring pool size to ${DEFAULT_POOL_SIZE}"
    call "/debug/inject/n-plus-one" '{"enabled":false}'
    call "/debug/inject/busy-loop" '{"enabled":false}'
    call "/debug/inject/timeout" '{"enabled":false}'
    call "/debug/inject/bad-cardinality" '{"enabled":false}'
    call "/debug/pool/resize" "{\"size\":${DEFAULT_POOL_SIZE}}"
    call "/debug/status"
    ;;
  *)
    cat >&2 <<'USAGE'
usage:
  ./inject.sh status
  ./inject.sh n-plus-one|busy-loop|timeout|bad-cardinality on|off
  ./inject.sh pool-size <n>
  ./inject.sh stop
USAGE
    exit 1
    ;;
esac
