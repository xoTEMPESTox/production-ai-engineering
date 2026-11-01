# Lesson 23: Docker

Learn how to package an AI service so it starts the same way everywhere: multi-stage builds, a non-root user, health checks that separate "alive" from "ready", artifacts like retrieval indexes baked in or mounted with a checksum, and a policy check that catches bad Dockerfiles in CI.

> **Pillars:** Harness · RAG  |  **Harness layer:** packaging, meaning what exactly runs in production and how the platform knows it's healthy
>
> *Small image, no root, no secrets inside, and don't report ready until the index checks out.*

## Why does this exist?
"It works on my machine" is worse for AI services than for most software, because they depend on more than code: pinned library versions, a system prompt and its version, and **data artifacts**, such as a retrieval index or embedding files that must match the code that reads them. A container image freezes the code and its dependencies, and can carry or reference the artifacts, so a deploy runs exactly what was tested.

A careless image creates its own production problems:
- an image several gigabytes large that is slow to pull and to scale;
- a process running as root;
- an API key in a layer;
- a container that reports healthy while it can't answer a single query, because the index volume wasn't mounted.

---

## What problem does it solve?
1. **Reproducibility**: the same dependencies, artifacts and start command in every environment.
2. **Size and speed**: small images pull fast, which matters when autoscaling (Lesson 30).
3. **Least privilege**: a compromised process shouldn't have root in its container.
4. **Health**: the platform must know when to restart a container, and when to send it traffic.
5. **Artifact integrity**: the index the service loads is the one it was tested with.

---

## How would beginners implement this?
```dockerfile
FROM python:latest
COPY . /app
RUN pip install -r /app/requirements.txt
ENV OPENAI_API_KEY=sk-live-123
CMD uvicorn example:app --host 0.0.0.0
```

### Why does that fail?
1. **`latest` moves.** Next week's build gets a different Python, and nothing in the repo changed.
2. **`COPY .` before `pip install`** invalidates the dependency layer on every code change, so every build reinstalls everything. It also copies `.git`, `.venv` and `.env` into the image.
3. **One stage**: compilers, caches and build tools ship to production, making the image bigger with more to attack.
4. **The API key is in the image.** Anyone who can pull the image can read it from its history, forever.
5. **It runs as root.**
6. **Shell-form `CMD`** runs under `/bin/sh -c`, which may not pass `SIGTERM` to the app, so shutdowns are hard kills and in-flight requests are dropped.
7. **No health check**, and if one were added it would be a single "is the port open?" test that passes even when the index is missing.

---

## How is this solved in production?
This lesson ships a real `Dockerfile`, `.dockerignore`, pinned `requirements.txt` and a small `index/` with a checksum manifest. Building the image needs Docker, but everything that can be tested without Docker is tested.

**1. Multi-stage, pinned, cache-friendly.** The build stage creates a virtualenv and installs the pinned requirements, copying *only* `requirements.txt` first so code changes don't invalidate that layer. The runtime stage starts again from a slim base and copies in just the virtualenv, the code and the index. The base image is pinned to a version tag; pin a digest when you need byte-for-byte rebuilds.

**2. Non-root, exec form, no secrets.** `useradd` plus `USER app`, so the process isn't root. `CMD ["uvicorn", "example:create_app", "--factory", ...]` is exec form, so `SIGTERM` reaches uvicorn and it shuts down gracefully. Secrets arrive at run time as environment variables from the platform's secret store (Lesson 25), never as `ENV` or `ARG`. If a build step needs one, use a build-time secret mount, which isn't stored in a layer. `.dockerignore` keeps `.env`, `.git` and `.venv` out of the build context entirely.

**3. Liveness and readiness are different questions.**
* `/healthz` (liveness): *is the process answering?* It checks nothing else, because restarting the container won't fix a missing index.
* `/readyz` (readiness): *can it serve?* It returns 503 with a reason until the index is loaded and verified.
The Dockerfile's `HEALTHCHECK` calls `/healthz`. Kubernetes ignores `HEALTHCHECK` and uses its own liveness, readiness and startup probes, which map onto these two endpoints.

**4. Verify artifacts before serving.** `load_index` reads `index/manifest.json` (version, file name, sha256) and refuses an index whose bytes don't match. A missing manifest (a volume that wasn't mounted) and a tampered file both leave the service *not ready*, rather than crashing or serving wrong answers. Every `/search` response carries `index_version`, so traces (Lesson 22) record which index produced an answer. A lesson-local `.gitattributes` stops git from rewriting the index's line endings, which would otherwise change its checksum on some checkouts.

**5. Bake in or mount**, chosen per artifact:
```dockerfile
COPY --chown=app:app index/ ./index/              # baked: versioned with the code
# or: docker run -v /srv/index:/app/index:ro ...  # mounted: updated without a rebuild
```

**6. Enforce it in CI.** `lint_dockerfile` checks the rules above (multi-stage, pinned base, non-root, `HEALTHCHECK`, exec form, no secret-looking `ENV`/`ARG`, dependencies installed before the code is copied), and `lint_dockerignore` checks the exclusions. A test runs both against this lesson's own files, so the shipped Dockerfile can't drift from the policy.

---

## Design Tradeoffs

### Baking artifacts in vs. mounting them
* **Baked in**: the image *is* the release, so code and index can't get out of sync. Cold starts don't wait on a download. Right for small, slowly changing artifacts such as a modest retrieval index or prompt files.
* **Mounted or downloaded at start**: update the data without a rebuild, and keep images small. Right for large model weights or indexes that change daily. You need a version check and a readiness gate, because the code and the data can now disagree.

### Slim vs. distroless vs. full base images
* **Full** (with a shell and build tools): easy to debug, but large, with more to patch.
* **Slim** (this lesson): a small Debian base with a shell. A common middle ground.
* **Distroless or minimal**: the smallest attack surface and no shell. Debugging needs different tools, and some Python wheels need system libraries.

### One container per role vs. one container for everything
* **Per role** (API, worker, scheduler, each its own command, often the same image): each scales and restarts on its own (Lessons 17 and 30).
* **Everything in one** (several processes under a supervisor): simpler for a demo, but one crash takes everything down and nothing scales separately.

---

## Failure Cases to Watch For
1. **Unpinned bases and dependencies**: builds change without any change in the repo.
2. **Secrets in layers** (`ENV`, `ARG`, or a copied `.env`): leaked to anyone who can pull the image.
3. **Running as root.**
4. **Shell-form entrypoints**: `SIGTERM` doesn't reach the app, so in-flight requests are cut at every deploy.
5. **Health checks that check the wrong thing**: liveness checks that call dependencies (causing restart storms when a database blips), or readiness that ignores the index.
6. **Artifacts that don't match the code**: a mounted index from another version, or no checksum check. A related trap: a new, empty *named* Docker volume is pre-filled with the image's own files at that path, so "mounted" can quietly mean "the baked-in copy". Check `index_version` on `/readyz` after every mount change.
7. **Multi-gigabyte images** with model weights baked in that change often: slow pulls and slow scale-up.
8. **Cache-busting layer order**: every commit reinstalls every dependency.

---

## Try It
```sh
cd part-2-production-patterns/23-docker
python example.py            # lints the Dockerfiles and checks the index; no Docker needed
python -m pytest tests.py -q
# with Docker installed:
docker build -t paie-lesson-23 .
docker run --rm -p 8000:8000 paie-lesson-23      # then open /readyz and /search?q=refund
```
What to watch for: this lesson's `Dockerfile` and `.dockerignore` produce no findings, while the naive Dockerfile triggers all seven rules. The baked-in index loads as version `2026-11-01.1`, and pointing the service at an empty folder (a volume that wasn't mounted) gives `ready=False` with a reason.

Experiment: in `load_index`, skip the checksum comparison (`if digest != manifest["sha256"]:` → `if False:`) and run the tests. `test_tampered_index_is_not_ready` fails: a hand-edited index is now served as if it were the release.

**Next:** Lesson 24 ships new prompts and models safely: canary and shadow deploys behind eval gates, rollback, and cold starts.
