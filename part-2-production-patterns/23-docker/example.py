"""
Lesson 23: Docker.

Packaging an AI service so it starts the same way everywhere. Two parts:
the service verifies its retrieval index against a checksum manifest before
it reports ready (whether the index was baked into the image or mounted at
run time), and keeps liveness (/healthz: the process answers) separate from
readiness (/readyz: it can serve). And a Dockerfile policy check for CI:
multi-stage build, pinned base image, non-root user, health check,
exec-form CMD, no secrets in the image, dependencies installed before the
code is copied.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse

HERE = Path(__file__).parent


# --- Part 1: a service that knows whether it can serve ---

class IndexInvalid(Exception):
    pass


def load_index(index_dir: Path) -> tuple[str, dict[str, str]]:
    """Read manifest.json, check the index file's sha256, return (version, documents)."""
    manifest_path = index_dir / "manifest.json"
    if not manifest_path.exists():
        raise IndexInvalid(f"no manifest at {manifest_path} (was the volume mounted?)")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    data = (index_dir / manifest["file"]).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != manifest["sha256"]:
        raise IndexInvalid(f"checksum mismatch for {manifest['file']}: index and manifest disagree")
    return manifest["version"], json.loads(data)


@dataclass
class Service:
    index_dir: Path
    ready: bool = False
    reason: str = "starting"
    version: Optional[str] = None
    documents: dict[str, str] = field(default_factory=dict)

    def start(self) -> None:
        """Runs before the app accepts traffic. A bad index means 'not ready', not 'crash'."""
        try:
            self.version, self.documents = load_index(self.index_dir)
            self.ready, self.reason = True, "ok"
        except (IndexInvalid, OSError, KeyError, json.JSONDecodeError) as exc:
            self.ready, self.reason = False, f"{type(exc).__name__}: {exc}"


def create_app(index_dir: Optional[Path] = None) -> FastAPI:
    service = Service(index_dir or Path(os.environ.get("INDEX_DIR", HERE / "index")))
    service.start()
    app = FastAPI()

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "alive"}  # no dependency checks: a restart wouldn't fix them

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        body = {"ready": service.ready, "reason": service.reason, "index_version": service.version}
        return JSONResponse(body, status_code=200 if service.ready else 503)

    @app.get("/search")
    def search(q: str) -> dict:
        hits = [doc_id for doc_id, text in service.documents.items() if q.lower() in text.lower()]
        return {"index_version": service.version, "hits": hits}

    return app


# --- Part 2: a Dockerfile policy check for CI ---

@dataclass(frozen=True)
class Finding:
    rule: str
    message: str


SECRET_NAMES = re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD)", re.I)


def lint_dockerfile(text: str) -> list[Finding]:
    joined = re.sub(r"\\\s*\n", " ", text)  # one instruction per line, even with "\" continuations
    lines = [ln.strip() for ln in joined.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    upper = [ln.upper() for ln in lines]
    findings: list[Finding] = []
    froms = [ln for ln in lines if ln.upper().startswith("FROM ")]
    if len(froms) < 2:
        findings.append(Finding("multi-stage", "use a build stage and a slim runtime stage"))
    for ln in froms:
        image = ln.split()[1]
        if ":" not in image or image.endswith(":latest"):
            findings.append(Finding("pinned-base", f"{image}: pin a version tag (or a digest), never latest"))
    users = [ln for ln in lines if ln.upper().startswith("USER ")]
    if not users or users[-1].split()[1] in ("root", "0"):
        findings.append(Finding("non-root", "the final stage must switch to a non-root USER"))
    if not any(ln.startswith("HEALTHCHECK ") for ln in upper):
        findings.append(Finding("healthcheck", "add a HEALTHCHECK so the platform can detect a hung process"))
    cmds = [ln for ln in lines if ln.upper().startswith(("CMD ", "ENTRYPOINT "))]
    if not cmds or not cmds[-1].split(None, 1)[1].startswith("["):
        findings.append(Finding("exec-form", "use exec-form CMD [...] so SIGTERM reaches the app"))
    for ln in lines:
        if ln.upper().startswith(("ENV ", "ARG ")) and SECRET_NAMES.search(ln):
            findings.append(Finding("no-secrets", f"secret-looking build value baked into the image: {ln}"))
    copy_all = next((i for i, ln in enumerate(upper) if re.match(r"COPY (--\S+ )*\. ", ln)), None)
    install = next((i for i, ln in enumerate(upper) if "PIP INSTALL" in ln), None)
    if copy_all is not None and install is not None and copy_all < install:
        findings.append(Finding("layer-order", "copy requirements and install them before copying the code"))
    return findings


def lint_dockerignore(text: str) -> list[Finding]:
    entries = {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}
    required = {".env", ".git", ".venv", "__pycache__"}
    return [Finding("dockerignore", f"{name} must be excluded from the build context")
            for name in sorted(required - entries)]


NAIVE_DOCKERFILE = """FROM python:latest
COPY . /app
RUN pip install -r /app/requirements.txt
ENV OPENAI_API_KEY=sk-live-123
CMD uvicorn example:app --host 0.0.0.0
"""

if __name__ == "__main__":
    print("this lesson's Dockerfile:", lint_dockerfile((HERE / "Dockerfile").read_text()) or "no findings")
    print("its .dockerignore:", lint_dockerignore((HERE / ".dockerignore").read_text()) or "no findings")
    print("\na naive Dockerfile:")
    for finding in lint_dockerfile(NAIVE_DOCKERFILE):
        print(f"  [{finding.rule}] {finding.message}")

    good = Service(HERE / "index")
    good.start()
    print(f"\nbaked-in index: ready={good.ready}, version={good.version}, docs={len(good.documents)}")
    missing = Service(HERE / "not-mounted")
    missing.start()
    print(f"forgot to mount the volume: ready={missing.ready}, reason={missing.reason}")
