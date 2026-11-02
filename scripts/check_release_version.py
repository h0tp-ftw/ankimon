"""Check that a release tag and the manifest it ships agree on the version.

The add-on reads its own version from src/Ankimon/manifest.json (resources.py
``addon_ver``). There are two ways that file reaches users, and they can disagree:

- The built .ankiaddon (AnkiWeb / "Install from file"). ``aab build`` rewrites the
  manifest from the tag, so this copy is right even when the repo's copy is stale.
- The tag's source tree. The in-app updater installs a release from the tag's
  ``zipball_url``, and git clones read it directly. This copy is whatever was
  committed, so it is ONLY right if the version bump landed before the tag.

release.yml does the bump (prepare_release.py) and then tags the bump commit, so
the two agree. A tag cut by hand skips the bump: 2.4.3-E shipped a correct
.ankiaddon from a tree whose manifest still said 2.4.2-E. This script catches
that, and is run both inside release.yml and by release-version-check.yml (which
sees the manual tags and releases that release.yml never runs for).

Checks, for --tag X:
  1. manifest.json at the tag's tree has version X
  2. assets/changelogs/X.md exists at the tag's tree (changelog.py fetches it)
  3. every --addon package's manifest.json has version X

Stdlib only, so it runs on a bare runner before any pip install.
"""

import argparse
import json
import re
import subprocess
import sys
import zipfile

MANIFEST_PATH = "src/Ankimon/manifest.json"

# Same shape as prepare_release.py's _VERSION_RE (kept in sync by hand; that
# module imports `requests`, which this script deliberately avoids).
_VERSION_RE = r"^v?\d+(\.\d+)*(-[A-Z])?$"


def _error(msg: str) -> None:
    # ::error:: renders as an annotation on the workflow run.
    print(f"::error::{msg}")


def _git_show(ref: str, path: str):
    """Return the file's text at ``ref``, or None if it doesn't exist there."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout if result.returncode == 0 else None


def _git_has(ref: str, path: str) -> bool:
    return (
        subprocess.run(
            ["git", "cat-file", "-e", f"{ref}:{path}"],
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )


def _manifest_version(text: str):
    try:
        return json.loads(text).get("version")
    except (ValueError, AttributeError):
        return None


def check_tag_tree(tag: str, ref: str, require_changelog: bool) -> list:
    version = tag.lstrip("v")
    problems = []

    text = _git_show(ref, MANIFEST_PATH)
    if text is None:
        problems.append(f"{MANIFEST_PATH} not found at {ref}.")
    else:
        found = _manifest_version(text)
        if (found or "").lstrip("v") != version:
            problems.append(
                f"{MANIFEST_PATH} at {ref} says version {found!r}, but the tag is "
                f"{tag!r}. In-app updates and git installs of this tag will report "
                f"the wrong version. Release through release.yml, which bumps the "
                f"manifest before tagging."
            )
        else:
            print(f"OK: {MANIFEST_PATH} at {ref} is {version}")

    changelog = f"assets/changelogs/{version}.md"
    if _git_has(ref, changelog):
        print(f"OK: {changelog} exists at {ref}")
    elif require_changelog:
        problems.append(
            f"{changelog} not found at {ref}; the in-app 'what's new' fetch will 404."
        )
    else:
        print(f"::warning::{changelog} not found at {ref}")

    return problems


def check_addon(tag: str, path: str) -> list:
    version = tag.lstrip("v")
    try:
        with zipfile.ZipFile(path) as zf:
            found = _manifest_version(zf.read("manifest.json").decode("utf-8"))
    except (OSError, KeyError, zipfile.BadZipFile) as e:
        return [f"could not read manifest.json from {path}: {e}"]
    if (found or "").lstrip("v") != version:
        return [f"{path} manifest says version {found!r}, but the tag is {tag!r}."]
    print(f"OK: {path} manifest is {version}")
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True, help="release tag, e.g. 2.4.3-E")
    parser.add_argument(
        "--ref",
        help="git ref to read the tree from (default: refs/tags/<tag>)",
    )
    parser.add_argument(
        "--addon",
        nargs="*",
        default=[],
        help=".ankiaddon packages whose bundled manifest must match the tag",
    )
    parser.add_argument(
        "--require-changelog",
        action="store_true",
        help="fail (rather than warn) when assets/changelogs/<tag>.md is missing",
    )
    parser.add_argument(
        "--skip-non-version",
        action="store_true",
        help="exit 0 for tags that aren't add-on versions (e.g. sprites-v1.43)",
    )
    args = parser.parse_args(argv)

    tag = args.tag.strip()
    if not re.match(_VERSION_RE, tag):
        if args.skip_non_version:
            print(f"{tag!r} is not an add-on version tag; nothing to check.")
            return 0
        _error(f"{tag!r} is not a valid version tag (expected e.g. 2.4.3-E).")
        return 1

    ref = args.ref or f"refs/tags/{tag}"
    problems = check_tag_tree(tag, ref, args.require_changelog)
    for addon in args.addon:
        problems += check_addon(tag, addon)

    for p in problems:
        _error(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
