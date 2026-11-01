# Lesson 23: Docker - Architecture

This document shows the two build stages, the startup and readiness sequence, the components, and the naive and production images side by side.

## Two Stages, One Small Image

```mermaid
flowchart LR
    subgraph Build["build stage: python 3.12 slim"]
        R[requirements.txt, pinned] --> V[pip install into /opt/venv]
    end
    subgraph Runtime["runtime stage: python 3.12 slim"]
        U[useradd app, uid 10001]
        V2["/opt/venv, copied from the build stage"]
        C[example.py]
        I[index/ baked in, or mounted read-only]
        H[HEALTHCHECK calls /healthz]
        S[USER app, then exec-form CMD uvicorn --factory]
    end
    V --> V2
    Ctx[build context minus .dockerignore: no .env, .git, .venv] --> C
    Ctx --> I
```

Compilers, pip caches and the build stage's leftovers never reach the runtime image.

---

## Startup and Readiness

```mermaid
sequenceDiagram
    participant P as Platform
    participant C as Container (uvicorn)
    participant S as Service.start
    participant I as /app/index
    P->>C: start container
    C->>S: create_app() calls start() before serving
    S->>I: read manifest.json
    alt manifest missing (volume not mounted)
        S-->>C: ready = False, reason = no manifest
    else checksum mismatch
        S-->>C: ready = False, reason = checksum mismatch
    else ok
        S-->>C: ready = True, index_version = 2026-11-01.1
    end
    P->>C: GET /healthz (liveness)
    C-->>P: 200 alive, whatever the index state
    P->>C: GET /readyz (readiness)
    C-->>P: 200 when ready, otherwise 503 with the reason
    Note over P: traffic only to ready containers, restarts only for dead ones
```

---

## Component Layout

```
part-2-production-patterns/23-docker/
  Dockerfile         multi-stage, pinned, non-root, HEALTHCHECK, exec-form CMD
  .dockerignore      .env, .git, .venv, __pycache__, tests
  .gitattributes     index/** -text   (keep the checksummed bytes exact)
  requirements.txt   fastapi, uvicorn, pydantic: pinned
  index/
    index.json       the retrieval documents
    manifest.json    {"version", "file", "sha256"}
  example.py
    load_index(dir) -> (version, documents) | IndexInvalid
    Service: start(), ready, reason, version
    create_app(index_dir?) -> /healthz, /readyz, /search
    lint_dockerfile(text) / lint_dockerignore(text) -> [Finding(rule, message)]
```

---

## Naive vs. Production Image

| Concern | `FROM python:latest`, `COPY .`, one stage | This lesson |
|---|---|---|
| Base image | moves with every upstream push | pinned tag (digest for exact rebuilds) |
| Build cache | every code change reinstalls everything | dependencies in their own layer |
| What ships | compilers, caches, `.git`, `.env` | venv, code, index |
| Secrets | `ENV OPENAI_API_KEY=...` in the image history | injected at run time, never in a layer |
| User | root | uid 10001 |
| Shutdown | `/bin/sh -c` may swallow SIGTERM | exec form: graceful shutdown |
| Missing index volume | "healthy", every query fails | live but not ready: 503 with the reason |
| Tampered index | served | refused by the checksum |
