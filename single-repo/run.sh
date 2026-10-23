#!/usr/bin/env bash
# One-command lifecycle for the local GitHub status board.
#   ./run.sh start|stop|restart|status|logs|once
# Environment (all optional): GSB_PORT, GSB_HOST (preview); GITHUB_TOKEN (generation only)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$HERE/.run"
PID_FILE="$RUN_DIR/server.pid"
LOG_FILE="$RUN_DIR/server.log"
PORT="${GSB_PORT:-8790}"
HOST="${GSB_HOST:-127.0.0.1}"
mkdir -p "$RUN_DIR"

is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

case "${1:-start}" in
  start)
    if is_running; then
      echo "already running (pid $(cat "$PID_FILE")) at http://$HOST:$PORT/"
      exit 0
    fi
    nohup python3 "$HERE/server.py" --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 1
    if is_running; then
      echo "started pid $(cat "$PID_FILE") -> http://$HOST:$PORT/  (log: $LOG_FILE)"
    else
      echo "failed to start; last log lines:" >&2
      tail -n 20 "$LOG_FILE" >&2
      exit 1
    fi
    ;;
  stop)
    if is_running; then
      kill "$(cat "$PID_FILE")" && rm -f "$PID_FILE" && echo "stopped"
    else
      rm -f "$PID_FILE"; echo "not running"
    fi
    ;;
  restart)
    "$0" stop || true
    exec "$0" start
    ;;
  status)
    if is_running; then
      echo "running pid $(cat "$PID_FILE") at http://$HOST:$PORT/"
      curl -fsS "http://$HOST:$PORT/data/snapshot.json" && echo
    else
      echo "not running"; exit 1
    fi
    ;;
  logs)
    tail -n "${2:-50}" -f "$LOG_FILE"
    ;;
  once)
    shift
    exec python3 "$HERE/publish.py" "$@"
    ;;
  *)
    echo "usage: $0 {start|stop|restart|status|logs|once}" >&2
    exit 2
    ;;
esac
