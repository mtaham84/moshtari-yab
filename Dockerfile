FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
# PyPI mirror (server in Iran); override: docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirror-pypi.runflare.com/simple
ARG PIP_TRUSTED_HOST=mirror-pypi.runflare.com
RUN pip install --default-timeout=120 --retries 10 -r requirements.txt

# twitter-cli 0.8.5 + X web changes (2026-09): /i/jf/ homepage and live query ids; fails the build if it no longer applies
COPY docker/patch_twitter_cli.py docker/
RUN python docker/patch_twitter_cli.py

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