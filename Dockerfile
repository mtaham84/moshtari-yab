# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
RUN --mount=type=cache,target=/root/.cache/pip \
    env -u PIP_NO_CACHE_DIR pip install --default-timeout=120 --retries 10 -r requirements.txt

# twitter-cli 0.8.5: X صفحهٔ اصلی را عوض کرده؛ /i/jf/ هنوز ondemand.s را دارد (XClientTransaction PR #48)
RUN f=$(python -c "import twitter_cli.client as c; print(c.__file__)") \
 && sed -i 's#"https://x.com", headers=ct_headers#"https://x.com/i/jf/", headers=ct_headers#' "$f" \
 && grep -q 'x.com/i/jf/' "$f"

COPY . .

# Static files are baked into the image (served by WhiteNoise).
RUN DJANGO_DEBUG=False DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /app/data /app/media /app/output \
    && chown -R app:app /app/data /app/media /app/output \
    && chmod +x docker/entrypoint.sh

USER app
VOLUME ["/app/data", "/app/media"]
EXPOSE 8000
ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["web"]