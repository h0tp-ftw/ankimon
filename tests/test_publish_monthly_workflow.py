"""Exercise the actual publisher shell against disposable local Git repositories."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import textwrap
from uuid import NAMESPACE_DNS, uuid5

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/publish_monthly_challenge.yml"
GIT = shutil.which("git")
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    os.name == "nt" or not GIT or not BASH,
    reason="Publisher shell requires Bash and Git on a POSIX host",
)


def run_git(cwd, *args):
    """Run Git without user hooks/configuration in a disposable repository."""
    return subprocess.run(
        [GIT, "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull},
        timeout=15,
    ).stdout


def publisher_script():
    """Read the literal workflow run block without adding a YAML dependency."""
    workflow = WORKFLOW.read_text()
    step = workflow.split("      - name: Publish challenge\n", 1)[1]
    script = textwrap.dedent(step.split("        run: |\n", 1)[1])
    assert "${{" not in script, "Actions expressions must not become shell source"
    return script.replace(
        'remote="https://x-access-token:${APP_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"',
        'remote="$TEST_REMOTE"',
    )


@pytest.fixture
def repositories(tmp_path):
    """Seed a publisher checkout, remote main, and a concurrent writer checkout."""
    remote, runner, rival = (
        tmp_path / name for name in ("remote.git", "runner", "rival")
    )
    remote.mkdir()
    runner.mkdir()
    run_git(remote, "init", "--bare", "--initial-branch=main")
    run_git(runner, "init", "--initial-branch=main")
    run_git(runner, "config", "user.name", "Test")
    run_git(runner, "config", "user.email", "test@example.invalid")
    run_git(runner, "remote", "add", "origin", str(remote))
    catalogue = runner / "assets/challenges/monthly_challenges.json"
    catalogue.parent.mkdir(parents=True)
    historical = json.loads(
        (ROOT / "assets/challenges/monthly_challenges.json").read_text()
    )
    previous = next(entry for entry in historical if entry["month"] == "October 2026")
    catalogue.write_text(json.dumps([previous]))
    (runner / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/release_monthly_challenge.py", runner / "scripts")
    run_git(runner, "add", ".")
    run_git(runner, "commit", "-m", "Fixture")
    run_git(runner, "push", "origin", "main")
    run_git(tmp_path, "clone", str(remote), str(rival))
    run_git(rival, "config", "user.name", "Concurrent Writer")
    run_git(rival, "config", "user.email", "test@example.invalid")
    candidate = json.loads(json.dumps(previous))
    candidate.update(code="SYNTHETIC-NOVEMBER", month="November 2026")
    candidate.pop("previous_challenge_individual_id", None)
    candidate["pokemon"]["individual_id"] = str(uuid5(NAMESPACE_DNS, "workflow-test"))
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GITHUB_OUTPUT": str(tmp_path / "outputs"),
        "UPCOMING_MONTHLY_CHALLENGES": json.dumps([candidate]),
        "FORCE_RELEASE": "true",
        "TARGET_MONTH": "November 2026",
        "DRY_RUN": "false",
        "RELEASE_MONTH": "November 2026",
        "TEST_REMOTE": str(remote),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    subprocess.run(
        [sys.executable, "scripts/release_monthly_challenge.py"],
        cwd=runner,
        env=env,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return remote, runner, rival, env


@pytest.mark.parametrize(
    "race", [None, "unrelated", "predecessor", "already_published"]
)
def test_publish_retries_preserve_concurrent_main(repositories, race):
    """A rejected push must regenerate on main or recognize an already merged month."""
    remote, runner, rival, env = repositories
    path = "assets/challenges/monthly_challenges.json"
    if race:
        if race == "unrelated":
            (rival / "concurrent.txt").write_text("Do not lose this commit.\n")
        elif race == "predecessor":
            data = json.loads((rival / path).read_text())
            data[0]["pokemon"]["individual_id"] = str(
                uuid5(NAMESPACE_DNS, "corrected-predecessor")
            )
            (rival / path).write_text(json.dumps(data))
        else:
            shutil.copy(runner / path, rival / path)
        run_git(rival, "add", ".")
        run_git(rival, "commit", "-m", "Concurrent update")
        hook = runner / ".git/hooks/pre-push"
        hook.write_text(
            "#!/bin/sh\nset -eu\n"
            'rm -- "$0"\n'
            f"{shlex.quote(GIT)} -C {shlex.quote(str(rival))} push origin main\n"
        )
        hook.chmod(0o755)
    result = subprocess.run(
        [BASH, "-c", publisher_script()],
        cwd=runner,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads(run_git(remote, "show", f"main:{path}"))
    assert [entry["month"] for entry in data] == ["October 2026", "November 2026"]
    assert (
        data[1]["previous_challenge_individual_id"]
        == data[0]["pokemon"]["individual_id"]
    )
    if race == "unrelated":
        assert (
            run_git(remote, "show", "main:concurrent.txt")
            == "Do not lose this commit.\n"
        )
    if race == "already_published":
        assert (
            run_git(remote, "log", "-1", "--format=%s").strip() == "Concurrent update"
        )
    assert run_git(runner, "status", "--porcelain") == ""


def test_publish_output_is_data_not_shell_source(repositories):
    """Even shell metacharacters in an output must remain literal commit text."""
    remote, runner, _, env = repositories
    env["RELEASE_MONTH"] = 'November $(touch injected) `touch backtick` "2026"'
    result = subprocess.run(
        [BASH, "-c", publisher_script()],
        cwd=runner,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (runner / "injected").exists()
    assert not (runner / "backtick").exists()
    assert env["RELEASE_MONTH"] in run_git(remote, "log", "-1", "--format=%s")


def test_push_failure_is_not_reported_as_success(repositories):
    """A permanent remote rejection fails after the bounded retry count."""
    remote, runner, _, env = repositories
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    result = subprocess.run(
        [BASH, "-c", publisher_script()],
        cwd=runner,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert "after three attempts" in result.stdout
    data = json.loads(
        run_git(remote, "show", "main:assets/challenges/monthly_challenges.json")
    )
    assert len(data) == 1
