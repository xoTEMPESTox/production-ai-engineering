"""
System: Coding Agent.

An agent that fixes a failing test without being able to cheat or escape.
It works in a disposable copy of the project (the role a git worktree plays),
so the original is never touched. File tools refuse paths outside the
workspace, and test files are read-only to the agent: the fix must be in the
code, not in the test. The only command it can run is the test suite, in a
subprocess with a timeout and an environment stripped of secrets. The loop
is test, read the failure, patch, test again, bounded by an attempt budget,
and the result is a unified diff for a person to review, never an automatic
merge.
"""
from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

PROJECT = {
    "pricing.py": (
        "def apply_discount(price_cents: int, percent: int) -> int:\n"
        '    """Price after a percentage discount, rounded down to whole cents."""\n'
        "    return price_cents - price_cents * percent // 10\n"
    ),
    "test_pricing.py": (
        "import unittest\n"
        "from pricing import apply_discount\n\n\n"
        "class TestDiscount(unittest.TestCase):\n"
        "    def test_ten_percent(self):\n"
        "        self.assertEqual(apply_discount(2000, 10), 1800)\n\n"
        "    def test_no_discount(self):\n"
        "        self.assertEqual(apply_discount(999, 0), 999)\n\n\n"
        'if __name__ == "__main__":\n'
        "    unittest.main()\n"
    ),
}


class ToolDenied(Exception):
    pass


@dataclass(frozen=True)
class TestRun:
    passed: bool
    output: str


class Workspace:
    """A disposable copy of the project. Everything the agent does happens here."""

    def __init__(self, source: Path) -> None:
        self.source = source
        self.root = Path(tempfile.mkdtemp(prefix="agent-ws-"))
        shutil.copytree(source, self.root, dirs_exist_ok=True)

    def _resolve(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if self.root.resolve() not in path.parents and path != self.root.resolve():
            raise ToolDenied(f"{relative!r} is outside the workspace")
        return path

    def read(self, relative: str) -> str:
        return self._resolve(relative).read_text(encoding="utf-8")

    def write(self, relative: str, content: str) -> None:
        path = self._resolve(relative)
        if path.name.startswith("test_"):
            raise ToolDenied(f"{relative} is a test: tests are read-only, fix the code instead")
        path.write_text(content, encoding="utf-8")

    def run_tests(self, timeout_s: float = 30.0) -> TestRun:
        """The only command the agent may run: the test suite, isolated."""
        try:
            done = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=self.root, env=sandbox_env(),
                                  capture_output=True, text=True, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return TestRun(False, f"tests timed out after {timeout_s}s")
        return TestRun(done.returncode == 0, (done.stdout + done.stderr)[-2000:])  # bounded output

    def diff(self) -> str:
        chunks = []
        for path in sorted(self.root.rglob("*.py")):
            relative = path.relative_to(self.root).as_posix()
            before = (self.source / relative).read_text(encoding="utf-8").splitlines(keepends=True)
            after = path.read_text(encoding="utf-8").splitlines(keepends=True)
            chunks += difflib.unified_diff(before, after, f"a/{relative}", f"b/{relative}")
        return "".join(chunks)

    def discard(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def sandbox_env() -> dict[str, str]:
    """Only what Python needs to start. No API keys, tokens or credentials reach the child process."""
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP")  # SYSTEMROOT is needed on Windows
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@dataclass(frozen=True)
class Patch:
    path: str
    content: str
    note: str


class CoderLLM(Protocol):
    def propose(self, failure: str, files: dict[str, str]) -> Patch: ...


class ScriptedCoder:
    """Deterministic stand-in: first tries to make the test pass by editing the test,
    then a wrong fix, then the right one. A real model plugs in behind the same Protocol."""

    def __init__(self) -> None:
        self.attempts = 0

    def propose(self, failure: str, files: dict[str, str]) -> Patch:
        self.attempts += 1
        if self.attempts == 1:
            return Patch("test_pricing.py", files["test_pricing.py"].replace("1800)", "0)"),
                         "make the test expect the current output")
        original = PROJECT["pricing.py"]
        if self.attempts == 2:
            return Patch("pricing.py", original.replace("// 10\n", "// 1000\n"), "scale was off")
        return Patch("pricing.py", original.replace("// 10\n", "// 100\n"), "percent means hundredths")


@dataclass
class Outcome:
    fixed: bool
    attempts: int
    diff: str
    log: list[str] = field(default_factory=list)


def repair(project: Path, coder: CoderLLM, max_attempts: int = 4) -> Outcome:
    workspace = Workspace(project)
    log: list[str] = []
    try:
        result = workspace.run_tests()
        log.append(f"baseline: {'pass' if result.passed else 'fail'}")
        attempts = 0
        feedback = result.output
        while not result.passed and attempts < max_attempts:
            attempts += 1
            files = {p.name: workspace.read(p.name) for p in sorted(workspace.root.glob("*.py"))}
            patch = coder.propose(feedback, files)
            try:
                workspace.write(patch.path, patch.content)
            except ToolDenied as exc:
                log.append(f"attempt {attempts}: denied ({exc})")
                feedback = f"DENIED: {exc}"  # the refusal goes back to the model as the next observation
                continue
            result = workspace.run_tests()
            feedback = result.output
            log.append(f"attempt {attempts}: patched {patch.path} ({patch.note}) -> {'pass' if result.passed else 'fail'}")
        return Outcome(result.passed, attempts, workspace.diff() if result.passed else "", log)
    finally:
        workspace.discard()  # the original project was never written to


def make_project(folder: Path, files: Optional[dict[str, str]] = None) -> Path:
    for name, content in (files or PROJECT).items():
        (folder / name).write_text(content, encoding="utf-8")
    return folder


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        project = make_project(Path(tmp))
        outcome = repair(project, ScriptedCoder())
        for line in outcome.log:
            print(line)
        print(f"\nfixed={outcome.fixed} after {outcome.attempts} attempts; proposed diff for review:\n")
        print(outcome.diff)
        print("original untouched:", (project / "pricing.py").read_text().count("// 10\n") == 1)
        try:
            Workspace(project).read("../../etc/passwd")
        except ToolDenied as exc:
            print("path escape refused:", exc)
