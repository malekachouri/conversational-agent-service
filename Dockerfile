
# ---- Build stage: resolve and install dependencies into a virtualenv ----
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
# pip is not needed at runtime; dropping it shrinks the image and the CVE surface.
RUN pip install -r requirements.txt && pip uninstall -y pip

# ---- Runtime stage: just the interpreter, the venv and the app ----
FROM python:3.12-slim AS runtime

# The SQLite fallback lives in a writable data dir; compose/K8s set a Postgres URL.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DATABASE_URL="sqlite:////srv/data/agent.db"

# Remove the base image's own pip too, then create an unprivileged user.
RUN rm -rf /usr/local/lib/python3.12/site-packages/pip* /usr/local/bin/pip* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY --from=builder /opt/venv /opt/venv
WORKDIR /srv
RUN mkdir data && chown app:app data
COPY app ./app

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

CMD ["uvicorn", "--factory", "app.main:create_app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
