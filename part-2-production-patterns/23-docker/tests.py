import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from example import HERE, NAIVE_DOCKERFILE, Service, create_app, lint_dockerfile, lint_dockerignore


async def get(app, path: str, query: str = "") -> tuple[int, dict]:
    """Call the ASGI app directly (no server, no HTTP client)."""
    status, body, done = {}, [], asyncio.Event()

    async def receive() -> dict:
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            body.append(message.get("body", b""))

    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
             "query_string": query.encode(), "headers": [], "http_version": "1.1", "scheme": "http",
             "server": ("test", 80), "client": ("test", 1), "root_path": ""}
    await app(scope, receive, send)
    return status["code"], json.loads(b"".join(body))


def rules(text: str) -> set[str]:
    return {f.rule for f in lint_dockerfile(text)}


GOOD = """FROM python:3.12-slim AS build
RUN pip install -r requirements.txt
FROM python:3.12-slim
COPY --from=build /opt/venv /opt/venv
USER app
HEALTHCHECK CMD ["python", "-c", "print(1)"]
CMD ["uvicorn", "example:create_app", "--factory"]
"""


class TestDockerfilePolicy(unittest.TestCase):
    def test_this_lessons_files_follow_the_policy(self) -> None:
        self.assertEqual(lint_dockerfile((HERE / "Dockerfile").read_text()), [])
        self.assertEqual(lint_dockerignore((HERE / ".dockerignore").read_text()), [])

    def test_naive_dockerfile_breaks_every_rule(self) -> None:
        self.assertEqual(rules(NAIVE_DOCKERFILE), {"multi-stage", "pinned-base", "non-root", "healthcheck",
                                                   "exec-form", "no-secrets", "layer-order"})

    def test_each_rule_on_its_own(self) -> None:
        self.assertEqual(rules(GOOD), set())
        self.assertEqual(rules(GOOD.replace("USER app", "USER root")), {"non-root"})
        self.assertEqual(rules(GOOD.replace("3.12-slim AS build", "latest AS build")), {"pinned-base"})
        self.assertEqual(rules(GOOD.replace('CMD ["uvicorn", "example:create_app", "--factory"]',
                                            "CMD uvicorn example:app")), {"exec-form"})
        self.assertEqual(rules(GOOD + "ARG HF_TOKEN=abc\n"), {"no-secrets"})

    def test_continuation_lines_are_read_as_one_instruction(self) -> None:
        text = GOOD.replace('CMD ["uvicorn", "example:create_app", "--factory"]',
                            'CMD \\\n  ["uvicorn", "example:create_app", "--factory"]')
        self.assertEqual(rules(text), set())  # still exec form, just split over two lines

    def test_dockerignore_must_exclude_secrets_and_junk(self) -> None:
        missing = {f.message.split()[0] for f in lint_dockerignore("__pycache__\n")}
        self.assertEqual(missing, {".env", ".git", ".venv"})


class TestReadiness(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder)
        shutil.copytree(HERE / "index", self.folder / "index")

    def test_shipped_index_matches_its_manifest(self) -> None:
        service = Service(HERE / "index")
        service.start()
        self.assertTrue(service.ready, service.reason)  # also catches line-ending rewrites by git
        self.assertEqual(len(service.documents), 3)

    def test_tampered_index_is_not_ready(self) -> None:
        (self.folder / "index" / "index.json").write_text('{"billing-04": "edited by hand"}')
        service = Service(self.folder / "index")
        service.start()
        self.assertFalse(service.ready)
        self.assertIn("checksum mismatch", service.reason)

    def test_missing_mount_is_not_ready_and_not_a_crash(self) -> None:
        service = Service(self.folder / "nothing-here")
        service.start()
        self.assertFalse(service.ready)
        self.assertIn("was the volume mounted", service.reason)

    def test_liveness_stays_up_while_readiness_says_why_not(self) -> None:
        app = create_app(self.folder / "nothing-here")
        self.assertEqual(asyncio.run(get(app, "/healthz"))[0], 200)
        status, body = asyncio.run(get(app, "/readyz"))
        self.assertEqual(status, 503)
        self.assertFalse(body["ready"])

    def test_ready_service_serves_with_its_index_version(self) -> None:
        app = create_app(self.folder / "index")
        self.assertEqual(asyncio.run(get(app, "/readyz"))[0], 200)
        status, body = asyncio.run(get(app, "/search", "q=refunded"))
        self.assertEqual((status, body["hits"], body["index_version"]), (200, ["billing-04"], "2026-11-01.1"))

    def test_index_location_comes_from_the_environment(self) -> None:
        old = os.environ.get("INDEX_DIR")
        os.environ["INDEX_DIR"] = str(self.folder / "index")  # e.g. a mounted volume
        self.addCleanup(lambda: os.environ.pop("INDEX_DIR") if old is None else os.environ.update(INDEX_DIR=old))
        self.assertEqual(asyncio.run(get(create_app(), "/readyz"))[0], 200)


if __name__ == "__main__":
    unittest.main()
