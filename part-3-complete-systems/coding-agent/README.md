# System: Coding Agent

A complete agent that fixes a failing test inside a sandbox it can't escape or cheat: a disposable copy of the project, path containment, read-only tests, a single allowed command with a timeout and no secrets, a bounded test-and-repair loop, and a diff for a person to review.

> **Pillars:** Agent · Harness  |  **Built from:** Lessons 02, 04, 08, 09, 10, 14, 29
>
> *Let it try anything inside the box, make sure the box holds, and have a person merge the result.*

## Why does this exist?
A coding agent is the most capable kind of agent you can give someone, and the most dangerous. It reads files, writes files and runs commands, so the questions that are theoretical elsewhere become concrete:
- **Can it write outside the project?** Path traversal makes the answer yes, unless the harness says no.
- **Can it make the tests pass without fixing anything?** Agents optimising for "tests pass" will edit the test if they're allowed to. That is reward hacking, and it's common.
- **Can the code it runs read your API keys?** Yes, if the child process inherits your environment.
- **Can a bad attempt break your working copy?** Yes, if it works in place.

---

## What problem does it solve?
1. **Isolation**: every change happens in a copy, and the original stays untouched until a person accepts a diff.
2. **Containment**: file operations stay inside the workspace.
3. **Honest success**: "the tests pass" must mean the code was fixed, not that the test was changed.
4. **Safe execution**: only the test suite can run, with a timeout and without secrets.
5. **Bounded effort**: a hopeless run stops after a few attempts and offers nothing to merge.

---

## How would beginners implement this?
```python
def fix(repo):
    while True:
        result = subprocess.run("pytest", shell=True, cwd=repo, capture_output=True)  # inherits all env vars
        if result.returncode == 0:
            return "fixed!"
        cmd = llm(f"Tests failed:\n{result.stdout}\nGive a shell command to fix it.")
        subprocess.run(cmd, shell=True, cwd=repo)  # whatever the model says, in the real repo
```

### Why does that fail?
1. **It works in the real repository.** A bad attempt (or `rm -rf`) damages your working copy.
2. **It runs arbitrary shell commands** written by the model, with your permissions.
3. **Secrets leak.** The child processes inherit every environment variable, API keys included.
4. **Nothing stops it editing the test.** `sed -i 's/1800/1980/' test_pricing.py` "fixes" the failure.
5. **No timeout, no attempt limit.** One hanging test, or one stubborn model, runs forever.
6. **"Fixed!" goes straight in**, with no diff and no review.

---

## How is this solved in production?
| Concern | How | Lesson |
|---|---|---|
| Isolation | `Workspace` copies the project to a temporary folder (the role a git worktree or container plays) and `discard()`s it afterwards | 10 |
| Containment | every path is resolved and must stay under the workspace root; `../../etc/passwd` and absolute paths raise `ToolDenied` | 29 |
| No cheating | `test_*` files are read-only to the agent: "tests are read-only, fix the code instead" | 09 |
| One command | `run_tests()` is the only execution tool: `python -m unittest` with no shell, a timeout and bounded output | 04, 29 |
| No secrets | `sandbox_env()` passes only what Python needs to start; a test inside the sandbox confirms a fake API key isn't visible | 29 |
| Refusals are observations | a denied write is logged and becomes the next attempt's context; it never crashes the loop | 02, 14 |
| Bounded loop | test → read the failure → patch → test, at most `max_attempts` | 02 |
| Human merge | the result is a unified diff, offered only when the tests pass; a person reviews it | 08 |

The demo's run:
```
baseline: fail
attempt 1: denied (test_pricing.py is a test: tests are read-only, fix the code instead)
attempt 2: patched pricing.py (scale was off) -> fail
attempt 3: patched pricing.py (percent means hundredths) -> pass
```
followed by the one-line diff (`// 10` → `// 100`). The original project is untouched, and the path escape is refused.

`ScriptedCoder` is a deterministic stand-in that first tries to cheat, then guesses wrong, then fixes it, so every guard is exercised offline. A real model plugs in behind the same `CoderLLM` protocol.

---

## Design Tradeoffs

### A copied folder vs. a git worktree vs. a container
* **Copied folder** (this system): no dependencies and easy to reason about, but it's no protection against what the *code* does when it runs, beyond the scrubbed environment.
* **Git worktree**: cheap and branch-aware, and the diff and commit come naturally. Same process, same filesystem access.
* **Container or VM**: the strongest isolation (no network, a read-only base, resource limits). It costs more to start, and it's what to use when running untrusted generated code for real.

### Read-only tests vs. letting the agent write tests
* **Read-only** (this system): success can't be faked. The agent can't add the regression test a good engineer would add.
* **New tests allowed, existing ones protected**: lets the agent contribute coverage. Review new tests as carefully as code, because a weak new test proves nothing.

### An attempt budget vs. running until green
* **Budget** (this system): bounded cost and time, and a clean "couldn't fix it" outcome.
* **Until green**: occasionally solves harder problems, at an unbounded cost, and the longer it runs the more likely the patch is a hack. Pair long runs with Lesson 28's spend limits.

---

## Failure Cases to Watch For
1. **Working in place**: the agent's experiments damage the real repository.
2. **Arbitrary shell**: `shell=True` with model-written commands.
3. **Path traversal**: writes outside the project via `..`, absolute paths or symlinks.
4. **Reward hacking**: the test, the assertion or the test runner's configuration edited to pass.
5. **Inherited secrets** in child processes, or secrets in files inside the workspace.
6. **Unbounded execution**: no timeout on tests, no attempt limit, no output cap.
7. **Auto-merge**: green tests treated as approval.
8. **Diffs offered for failed runs**: a half-fix presented as a candidate.

---

## Try It
```sh
cd part-3-complete-systems/coding-agent
python example.py            # runs the real test suite in a temporary workspace
python -m pytest tests.py -q
```
What to watch for: the first attempt is denied because it targets the test file. The second patch fails, and the third passes. The diff touches only `pricing.py`, `original untouched: True`, and `../../etc/passwd` is refused.

Experiment: in `Workspace.write`, remove the `test_` check and run the demo. Attempt 1 now "succeeds": it edits the test to expect the buggy output, the suite passes, the loop stops, and the diff for review is a change to the test, with the bug still in the code. That's reward hacking, and it's why the guard exists.
