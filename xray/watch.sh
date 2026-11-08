#!/bin/sh
# Entrypoint of the docker-compose `xray` service: runs xray and restarts it when config.json changes.
#
# The app rewrites config.json (atomic replace) whenever V2Ray configs are added or die; until xray restarts, the
# new dedicated ports do not exist and accounts cannot use them. Restarts at most once per XRAY_RELOAD_MIN_SECONDS
# (default 60) so a burst of changes costs one short reconnect, not many: every restart drops all live connections.
CFG="${XRAY_CONFIG:-/etc/xray/config.json}"
MIN_GAP="${XRAY_RELOAD_MIN_SECONDS:-60}"
POLL="${XRAY_WATCH_POLL_SECONDS:-2}"
BIN="$(command -v xray || echo /usr/bin/xray)"
pid=""

fingerprint() { cksum "$CFG" 2>/dev/null; }
stop() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; exit 0; }
trap stop TERM INT

while true; do
  last="$(fingerprint)"
  started="$(date +%s)"
  "$BIN" run -c "$CFG" &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep "$POLL" &
    wait $!
    now="$(fingerprint)"
    if [ -n "$now" ] && [ "$now" != "$last" ] && [ $(( $(date +%s) - started )) -ge "$MIN_GAP" ]; then
      echo "xray-watch: $CFG changed, restarting xray"
      kill "$pid" 2>/dev/null
      break
    fi
  done
  wait "$pid" 2>/dev/null
  sleep 3
done
