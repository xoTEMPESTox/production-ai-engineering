import os
import tempfile
import unittest
from pathlib import Path

from example import PROJECT, Patch, ScriptedCoder, ToolDenied, Workspace, make_project, repair, sandbox_env


class TempProject(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def workspace(self, files: dict | None = None) -> Workspace:
        ws = Workspace(make_project(self.folder, files))
        self.addCleanup(ws.discard)
        return ws


class TestWorkspaceGuards(TempProject):
    def test_paths_outside_the_workspace_are_refused(self) -> None:
        ws = self.workspace()
        for path in ("../outside.py", "../../etc/passwd", str(self.folder.parent / "x.py")):
            with self.assertRaises(ToolDenied, msg=path):
                ws.read(path)

    def test_tests_are_read_only_to_the_agent(self) -> None:
        ws = self.workspace()
        with self.assertRaises(ToolDenied):
            ws.write("test_pricing.py", "# passes now")
        ws.write("pricing.py", PROJECT["pricing.py"])  # code is writable

    def test_original_project_is_never_modified(self) -> None:
        ws = self.workspace()
        ws.write("pricing.py", "broken = True\n")
        self.assertEqual((self.folder / "pricing.py").read_text(), PROJECT["pricing.py"])

    def test_child_process_gets_no_secrets(self) -> None:
        os.environ["PAIE_FAKE_API_KEY"] = "sk-should-not-leak"
        self.addCleanup(os.environ.pop, "PAIE_FAKE_API_KEY")
        self.assertNotIn("PAIE_FAKE_API_KEY", sandbox_env())
        probe = {"test_env.py": "import os, unittest\n\nclass T(unittest.TestCase):\n"
                                "    def test_no_key(self):\n"
                                "        self.assertNotIn('PAIE_FAKE_API_KEY', os.environ)\n"}
        self.assertTrue(self.workspace(probe).run_tests().passed)  # checked from inside the sandbox

    def test_hanging_tests_are_cut_off(self) -> None:
        slow = {"test_slow.py": "import time, unittest\n\nclass T(unittest.TestCase):\n"
                                "    def test_hang(self):\n        time.sleep(30)\n"}
        result = self.workspace(slow).run_tests(timeout_s=1)
        self.assertFalse(result.passed)
        self.assertIn("timed out", result.output)


class TestRepairLoop(TempProject):
    def test_fix_lands_in_the_code_not_the_test(self) -> None:
        outcome = repair(make_project(self.folder), ScriptedCoder())
        self.assertTrue(outcome.fixed)
        self.assertIn("denied", outcome.log[1])
        self.assertIn("+++ b/pricing.py", outcome.diff)
        self.assertNotIn("test_pricing.py", outcome.diff)
        self.assertIn("// 100", outcome.diff)

    def test_attempt_budget_ends_a_hopeless_run_with_nothing_to_merge(self) -> None:
        class Stubborn:
            def propose(self, failure, files):  # a real change, but a wrong one
                return Patch("pricing.py", PROJECT["pricing.py"].replace("// 10\n", "// 1000\n"), "guess")

        outcome = repair(make_project(self.folder), Stubborn(), max_attempts=2)
        self.assertEqual((outcome.fixed, outcome.attempts, outcome.diff), (False, 2, ""))

    def test_already_green_project_needs_no_attempts(self) -> None:
        good = {**PROJECT, "pricing.py": PROJECT["pricing.py"].replace("// 10\n", "// 100\n")}
        outcome = repair(make_project(self.folder, good), ScriptedCoder())
        self.assertEqual((outcome.fixed, outcome.attempts, outcome.diff), (True, 0, ""))

    def test_workspace_is_removed_afterwards(self) -> None:
        before = set(Path(tempfile.gettempdir()).glob("agent-ws-*"))
        repair(make_project(self.folder), ScriptedCoder())
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("agent-ws-*")), before)


if __name__ == "__main__":
    unittest.main()
