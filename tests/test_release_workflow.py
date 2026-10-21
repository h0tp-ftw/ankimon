"""Run only the release workflow's version resolver against disposable env files."""

import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

BASH = shutil.which("bash", path=os.defpath)
pytestmark = pytest.mark.skipif(
    os.name == "nt" or BASH is None,
    reason="Release resolver checks require a POSIX host with Bash",
)


def _extract_resolver(workflow):
    """Extract this literal run block without introducing a YAML dependency."""
    lines = workflow.splitlines(keepends=True)
    step = lines.index("      - name: Resolve release version\n")
    run = step + 1
    while lines[run] != "        run: |\n":
        assert not lines[run].startswith("      - "), "Resolver run block missing"
        run += 1
    script = []
    for line in lines[run + 1 :]:
        if line.strip() and not line.startswith("          "):
            break
        script.append(line)
    assert script, "Resolver run block is empty"
    return textwrap.dedent("".join(script))


@pytest.fixture(scope="module")
def resolver_script():
    workflow = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"
    return _extract_resolver(workflow.read_text(encoding="utf-8"))


def _run_resolver(script, env_path, raw_version, experimental):
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        env={
            "PATH": os.defpath,
            "LC_ALL": "C",
            "RAW_VERSION": raw_version,
            "IS_EXPERIMENTAL": experimental,
            "GITHUB_ENV": str(env_path),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    ("raw_version", "base_version"),
    [
        ("2", "2"),
        ("2.4", "2.4"),
        ("2.4.2", "2.4.2"),
        ("v2.4.2", "2.4.2"),
        ("2.4.2-E", "2.4.2"),
        ("v2.4.2-E", "2.4.2"),
    ],
)
@pytest.mark.parametrize(
    ("experimental", "suffix", "is_exp"),
    [
        ("true", "-E", "true"),
        ("True", "-E", "true"),
        ("false", "", "false"),
        ("False", "", "false"),
    ],
)
def test_resolver_preserves_version_and_channel_normalization(
    resolver_script, tmp_path, raw_version, base_version, experimental, suffix, is_exp
):
    env_path = tmp_path / "github-env"

    result = _run_resolver(resolver_script, env_path, raw_version, experimental)

    assert result.returncode == 0, result.stderr
    assert env_path.read_text() == f"VERSION={base_version}{suffix}\nIS_EXP={is_exp}\n"


@pytest.mark.parametrize(
    "raw_version",
    [
        "",
        "v",
        "V2.4.2",
        "vv2.4.2",
        "2.4.2-e",
        "2.4.2-EE",
        "2..4",
        "2.4.",
        ".2.4",
        "../2.4",
        "2.4 beta",
        " 2.4.2",
        "2.4.2 ",
        "\t2.4.2",
        "2.4.2\t",
        "2.4.2\n",
        "2.4.2\r",
        "2.4.2\r\n",
        "2.4.2\nRELEASE_TEST_SENTINEL=injected\nVERSION=2.4.3",
        "2.4.2\r\nRELEASE_TEST_SENTINEL=injected\r\nVERSION=2.4.3",
    ],
)
@pytest.mark.parametrize("experimental", ["true", "false"])
@pytest.mark.parametrize("existing_env", [None, b"RELEASE_TEST_SENTINEL=original\n"])
def test_resolver_rejects_invalid_input_before_any_environment_write(
    resolver_script, tmp_path, raw_version, experimental, existing_env
):
    env_path = tmp_path / "github-env"
    if existing_env is not None:
        env_path.write_bytes(existing_env)

    result = _run_resolver(resolver_script, env_path, raw_version, experimental)

    assert result.returncode != 0, result.stdout
    assert "::error::Invalid version" in result.stdout
    if existing_env is None:
        assert not env_path.exists()
    else:
        assert env_path.read_bytes() == existing_env
