#!/bin/sh
# One image, several roles: web | sync | engine | crawler | login | x | <any command>
set -e

wait_for_db() {
  python - <<'PY'
import os, sys, time
import psycopg
dsn = dict(host=os.environ.get("POSTGRES_HOST", "db"), port=os.environ.get("POSTGRES_PORT", "5432"),
           dbname=os.environ.get("POSTGRES_DB", "customer_yab"), user=os.environ.get("POSTGRES_USER", "postgres"),
           password=os.environ.get("POSTGRES_PASSWORD", ""))
for i in range(60):
    try:
        psycopg.connect(**dsn, connect_timeout=3).close()
        sys.exit(0)
    except Exception as exc:
        print(f"waiting for postgres... ({exc.__class__.__name__})", flush=True)
        time.sleep(2)
sys.exit("postgres is not reachable")
PY
}

case "$1" in
  web)
    wait_for_db
    python manage.py migrate --noinput
    if [ "${DJANGO_SEED_DEMO:-false}" = "true" ]; then
      python manage.py seed_products
    fi
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout "${GUNICORN_TIMEOUT:-60}" \
      --access-logfile - --error-logfile -
    ;;
  sync)
    wait_for_db
    exec python manage.py sync_opportunities --follow --interval "${SYNC_INTERVAL:-10}"
    ;;
  engine)
    shift
    wait_for_db
    exec python -m need_engine run "$@"
    ;;
  crawler)
    # Groups come from the panel («جوامع آنلاین»); an optional groups file is added on top.
    shift
    wait_for_db
    if [ "$#" -eq 0 ] && [ -f "${TG_GROUPS_FILE:-data/groups.txt}" ]; then
      set -- --links-file "${TG_GROUPS_FILE:-data/groups.txt}"
    fi
    exec python -m telegram_crawler.main "$@"
    ;;
  login)
    # docker compose run --rm crawler login   → interactive Telegram login, the session is kept in the appdata volume
    wait_for_db
    exec python -m telegram_crawler.main --login
    ;;
  x)
    shift
    [ "$#" -eq 0 ] && set -- --loop
    exec python -m workers.x_collector "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
