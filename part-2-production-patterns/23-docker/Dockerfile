# syntax=docker/dockerfile:1

# ---- build stage: compilers and caches stay here ----
FROM python:3.12-slim AS build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ---- runtime stage: only what the service needs ----
FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INDEX_DIR=/app/index
COPY --chown=app:app example.py ./
# Option A: bake the index into the image (immutable, versioned with the code, fast start).
# Option B: drop this line and mount it at run time: docker run -v /srv/index:/app/index:ro ...
COPY --chown=app:app index/ ./index/
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "example:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
