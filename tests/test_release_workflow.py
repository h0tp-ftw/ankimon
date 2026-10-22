"""Validate release version resolution and the publishing approval boundary."""

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


def _run_resolver(script, env_path, raw_version):
    return subprocess.run(
        [BASH, "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        env={
            "PATH": os.defpath,
            "LC_ALL": "C",
            "RAW_VERSION": raw_version,
            "GITHUB_ENV": str(env_path),
            "GITHUB_OUTPUT": str(env_path.with_suffix(".outputs")),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    ("raw_version", "version", "channel"),
    [
        ("2", "2", "stable"),
        ("2.4", "2.4", "stable"),
        ("2.4.2", "2.4.2", "stable"),
        ("v2.4.2", "2.4.2", "stable"),
        ("2.4.2-E", "2.4.2-E", "experimental"),
        ("v2.4.2-E", "2.4.2-E", "experimental"),
        ("2.4.2-e", "2.4.2-E", "experimental"),
        ("v2.4.2-e", "2.4.2-E", "experimental"),
    ],
)
def test_resolver_preserves_version_and_channel_normalization(
    resolver_script, tmp_path, raw_version, version, channel
):
    env_path = tmp_path / "github-env"

    result = _run_resolver(resolver_script, env_path, raw_version)

    assert result.returncode == 0, result.stderr
    assert env_path.read_text() == f"VERSION={version}\n"
    assert env_path.with_suffix(".outputs").read_text() == f"channel={channel}\n"


@pytest.mark.parametrize(
    "raw_version",
    [
        "",
        "v",
        "V2.4.2",
        "vv2.4.2",
        "2.4.2-E-E",
        "2.4.2-eE",
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
@pytest.mark.parametrize("existing_env", [None, b"RELEASE_TEST_SENTINEL=original\n"])
def test_resolver_rejects_invalid_input_before_any_environment_write(
    resolver_script, tmp_path, raw_version, existing_env
):
    env_path = tmp_path / "github-env"
    output_path = env_path.with_suffix(".outputs")
    if existing_env is not None:
        env_path.write_bytes(existing_env)
        output_path.write_bytes(existing_env)

    result = _run_resolver(resolver_script, env_path, raw_version)

    assert result.returncode != 0, result.stdout
    assert "::error::Invalid version" in result.stdout
    if existing_env is None:
        assert not env_path.exists()
        assert not output_path.exists()
    else:
        assert env_path.read_bytes() == existing_env
        assert output_path.read_bytes() == existing_env


def test_workflow_routes_only_explicit_channels_and_gates_stable():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"
    ).read_text(encoding="utf-8")
    build, stable = workflow.split("\n  publish-stable:\n", 1)
    stable, experimental = stable.split("\n  publish-experimental:\n", 1)

    # No independent input may override the channel implied by the version.
    assert "inputs.experimental" not in workflow
    assert "      experimental:" not in build
    assert "      channel: ${{ steps.resolve-version.outputs.channel }}" in build
    assert build.index("- name: Resolve release version") < build.index(
        "- name: Checkout code"
    )
    assert 'python scripts/prepare_release.py --version "$VERSION"' in build
    assert 'git tag "${VERSION}"' in build
    assert 'echo "release_tag=${VERSION}"' in build

    for channel, job in (("stable", stable), ("experimental", experimental)):
        assert f"    if: ${{{{ needs.build.outputs.channel == '{channel}' }}}}" in job
        assert "    needs: build\n" in job
        assert "          tag_name: ${{ needs.build.outputs.release_tag }}" in job
        assert "          name: ankiaddon\n" in job
        assert "          files: build/*.ankiaddon\n" in job
        assert "          fail_on_unmatched_files: true\n" in job
    assert "    environment:\n      name: ankiweb\n" in stable
    assert "    environment:" not in experimental
