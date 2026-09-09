"""Regression tests for the PR review helpers; runnable without Anki or Qt."""

import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "pr-reviewer"
sys.path.insert(0, str(ROOT))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load_module("pr_proof_runner", SKILL / "scripts" / "run_proof_scenario.py")
context = load_module("pr_context", SKILL / "scripts" / "fetch_pr_context.py")


class ProofRunnerTests(unittest.TestCase):
    def run_proof(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proof.py"
            path.write_text(source, encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(SKILL / "scripts" / "run_proof_scenario.py"),
                    "--file",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

    def test_rejects_unexecuted_main_guard(self):
        result = self.run_proof(
            "if __name__ == '__main__':\n    raise AssertionError('must run')\n"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("PASSED", result.stdout)

    def test_requires_explicit_success(self):
        for body in (
            "pass",
            "return False",
            "return 1",
            "raise AssertionError('failed')",
        ):
            with self.subTest(body=body):
                result = self.run_proof("def run_proof():\n    " + body + "\n")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("PASSED", result.stdout)

    def test_system_exit_does_not_bypass_proof(self):
        result = self.run_proof("raise SystemExit(0)\n")
        self.assertNotEqual(result.returncode, 0)

    def test_runs_entrypoint_before_reporting_success(self):
        result = self.run_proof(
            "def run_proof():\n    print('assertions ran')\n    return True\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("assertions ran", result.stdout)
        self.assertIn("PASSED", result.stdout)

    def test_smoke_checks_events_returned_by_real_driver(self):
        # Keep the real Driver.answer/drain_events contract; replace only session
        # boot to inject controlled battle output without booting the game.
        from harness.driver import Driver

        bus_module = load_module(
            "pr_review_events", ROOT / "src" / "Ankimon" / "events.py"
        )
        for event_types, expected in (
            (["battle", "error"], False),
            ([], False),
            (["battle"], True),
        ):
            with self.subTest(event_types=event_types):
                bus = bus_module._EventBus()
                bus.enable()
                env = SimpleNamespace(
                    events=bus,
                    services=SimpleNamespace(
                        tracker=SimpleNamespace(review=lambda grade: None)
                    ),
                    on_review_card=lambda: [bus.emit(kind) for kind in event_types],
                )
                with (
                    patch("harness.driver.start_session", return_value=env),
                    patch.object(Driver, "set_enemy", return_value=[]),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertIs(runner.run_smoke_tier1(), expected)

    def test_scaffolding_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "proof.py"
            target.write_text("# existing proof\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(runner.scaffold_test(1, target))
            self.assertEqual(target.read_text(), "# existing proof\n")

    def test_templates_find_checkout_from_nested_destination(self):
        # A tiny checkout layout lets the copied scaffold prove its import setup
        # from an unrelated cwd, independently of optional game dependencies.
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / "checkout"
            (checkout / "src" / "Ankimon").mkdir(parents=True)
            (checkout / "harness").mkdir()
            (checkout / "harness" / "__init__.py").write_text("")
            (checkout / "harness" / "driver.py").write_text(
                "MARKER = 'correct checkout'\n"
            )
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            for tier in (1, 2):
                with self.subTest(tier=tier):
                    target = checkout / "tests" / "proofs" / f"tier{tier}.py"
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertTrue(runner.scaffold_test(tier, target))
                    result = subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            "import runpy, sys; runpy.run_path(sys.argv[1]); "
                            "from harness.driver import MARKER; print(MARKER)",
                            str(target),
                        ],
                        cwd=directory,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("correct checkout", result.stdout)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.git("init", "-q")
        (self.repo / "example.txt").write_text("base\n")
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=Review Test",
            "-c",
            "user.email=review@example.invalid",
            "commit",
            "-qm",
            "base",
        )
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        )

    def run_context(self, base):
        return subprocess.run(
            [
                sys.executable,
                str(SKILL / "scripts" / "fetch_pr_context.py"),
                "--local",
                "--base",
                base,
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_missing_base_is_failure_not_low_risk(self):
        result = self.run_context("absent-base")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[LOW]", result.stdout)
        self.assertNotIn("Changed Files (0)", result.stdout)

    def test_empty_diff_is_success(self):
        result = self.run_context("main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Changed Files (0)", result.stdout)

    @unittest.skipIf(os.name == "nt", "POSIX shell regression")
    def test_git_valid_base_is_literal_not_shell(self):
        sentinel = self.repo / "injected"
        base = "main;touch${IFS}" + str(sentinel) + ";#"
        self.git("check-ref-format", "--branch", base)
        self.git("update-ref", "refs/remotes/origin/" + base, "HEAD")
        result = self.run_context(base)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists(), "base name executed as shell commands")

    def test_database_changes_are_classified_as_database(self):
        risk, subsystems = context.classify_risk(
            [{"path": "src/Ankimon/pyobj/database_manager.py"}]
        )
        self.assertEqual(risk, "Critical")
        self.assertEqual(subsystems, ["Database"])

    def test_remote_filename_with_spaces_is_preserved(self):
        with patch.object(
            context, "run_cmd", return_value="docs/a file with spaces.md\n"
        ):
            files = context.get_changed_files(pr_num=818)
        self.assertEqual([f["path"] for f in files], ["docs/a file with spaces.md"])


if __name__ == "__main__":
    unittest.main()
