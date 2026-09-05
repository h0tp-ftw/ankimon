import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.error
import zipfile
from pathlib import Path
from typing import Optional

from aqt import mw
from aqt.operations import QueryOp

from ..resources import addon_dir

REPO_OWNER = "h0tp-ftw"
REPO_NAME = "ankimon"
GITHUB_API = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
GIT_REMOTE_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}.git"
DOWNLOAD_TIMEOUT = 30
USER_AGENT = "Ankimon-Updater (https://github.com/h0tp-ftw/ankimon)"
DEFAULT_SUBMODULE_SHA = "f3092b03fbe1e37d1788ef802dee98906d621e36"
# The in-app updater shipped in v2.0. Installing an older release would strip the
# updater out (older versions predate it), so pre-2.0 versions are filtered from
# the release/tag pickers — going back would break the update feature itself.
MIN_UPDATER_VERSION = (2, 0)
# Refs that may be interpolated into an API path. Tags are version-shaped and
# always match; branch names often do not (``add/foo``, ``agent/bar``), and are
# deliberately refused rather than requested — the branch paths resolve a commit
# SHA first, so rejecting the name only costs an undated install, never a wrong one.
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _is_safe_ref(ref: Optional[str]) -> bool:
    """Whether ``ref`` may be interpolated into an API path.

    The character class above still admits the dot segments ``.`` and ``..``,
    which a URL normaliser resolves away *before* the request is sent — so
    ``commits/..`` would address the repo endpoint rather than a commit. No real
    Git ref can contain either (``git check-ref-format`` rejects a component
    starting with ``.`` and any ``..``), so refusing them costs nothing and lets
    the guard actually mean what it says: anything not plainly a ref fails
    closed, and the install goes undated rather than mis-dated.
    """
    if not ref or not _SAFE_REF_RE.match(ref):
        return False
    return not ref.startswith(".") and ".." not in ref


# Auto-update channels (user-selectable in the update dialog). "stable" and
# "experimental" are release channels told apart by the tag suffix — an
# experimental release tag ends in "-E" (e.g. "2.02-E"), a stable one does not
# (e.g. "2.03"). "main" tracks the main branch HEAD via the branch-SHA poll.
CHANNEL_STABLE = "stable"
CHANNEL_EXPERIMENTAL = "experimental"
CHANNEL_MAIN = "main"
UPDATE_CHANNELS = (CHANNEL_STABLE, CHANNEL_EXPERIMENTAL, CHANNEL_MAIN)


def _git_repo_root() -> Optional[Path]:
    """Return the git repo root that contains the addon, else None.

    For a GitHub clone the addon is ``src/Ankimon`` nested in the repo, so the
    ``.git`` directory sits two levels *above* ``addon_dir`` at the repo root —
    hence the upward parent walk. ``resolve()`` first follows the dev symlink
    from ``addons21/`` into the repo so those parents land on the real repo root;
    the walk is kept shallow to avoid false positives from an unrelated repo
    higher up.
    """
    try:
        base = Path(addon_dir).resolve()
    except Exception:
        base = Path(addon_dir)
    for d in [base, *list(base.parents)[:3]]:
        # .exists() rather than .is_dir(): in git worktrees and some submodule
        # layouts ".git" is a *file* (containing "gitdir: ..."), not a directory.
        if (d / ".git").exists():
            return d
    return None


def is_git_clone() -> bool:
    """True if Ankimon runs from a git working tree.

    Git detection is independent of developer mode: the archive installer must
    never overwrite a checkout. The updater dialog stays available for clones,
    but routes selections through safe Git operations instead.
    """
    return _git_repo_root() is not None


def get_git_checkout_info() -> dict:
    """Return lightweight display information for the updater's Git mode."""
    root = _git_repo_root()
    info = {"is_git": root is not None, "branch": "", "sha": "", "dirty": False}
    if root is None or shutil.which("git") is None:
        return info

    def _git(*args):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )

    try:
        branch = _git("rev-parse", "--abbrev-ref", "HEAD")
        sha = _git("rev-parse", "--short", "HEAD")
        status = _git("status", "--porcelain")
        if branch.returncode == 0:
            info["branch"] = branch.stdout.strip()
        if sha.returncode == 0:
            info["sha"] = sha.stdout.strip()
        if status.returncode == 0:
            info["dirty"] = bool(status.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def git_pull_ff_only(status_cb=None) -> tuple[bool, str]:
    """Update a dev clone via ``git pull --ff-only`` (+ submodule update).

    Safe by construction: ``--ff-only`` refuses, without changing anything, when
    the tree is dirty or the branch has diverged — so it never creates merge
    conflicts. Returns ``(success, message)``.
    """

    def log(msg):
        if status_cb:
            status_cb(msg)

    root = _git_repo_root()
    if root is None:
        return False, "Ankimon is not running from a git checkout."

    if shutil.which("git") is None:
        return False, (
            "git wasn't found on Anki's PATH. This is common when Anki is "
            "launched from the dock/Finder, which doesn't inherit your shell "
            "PATH. Restart Anki from a terminal, or run 'git pull' in your "
            "clone yourself."
        )

    def _git(*args, timeout=180):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        status = _git("status", "--porcelain", timeout=30)
        if status.returncode != 0:
            return False, (status.stderr or status.stdout or "git status failed").strip()
        if status.stdout.strip():
            return False, (
                "Your Ankimon checkout has local changes. Commit, stash, or discard "
                "them before updating from the updater."
            )

        branch = (
            _git("rev-parse", "--abbrev-ref", "HEAD", timeout=30).stdout or ""
        ).strip() or "HEAD"
        if branch == "HEAD":
            return False, (
                "This checkout is detached. Select a branch, release, tag, or PR in "
                "the updater instead of pulling the current checkout."
            )
        log(f"Fast-forwarding '{branch}' (git pull --ff-only)...")
        pull = _git("pull", "--ff-only")
        if pull.returncode != 0:
            err = (pull.stderr or pull.stdout or "").strip()
            return False, (
                f"Could not fast-forward '{branch}'. This is expected if you "
                "have local changes/commits or no upstream — resolve it manually "
                "with git.\n\n" + err
            )
        log("Updating submodules...")
        sub = _git("submodule", "update", "--init", "--recursive")
        out = (pull.stdout or "").strip()
        if sub.returncode != 0:
            return True, (
                f"Updated '{branch}', but the submodule update failed — run "
                "'git submodule update --init --recursive' manually.\n\n"
                + (sub.stderr or "").strip()
            )
        return True, f"Updated '{branch}' via git pull --ff-only.\n\n{out}"
    except subprocess.TimeoutExpired:
        return False, "git timed out. Update manually with 'git pull'."
    except Exception as e:
        return False, f"git update failed: {e}. Update manually with 'git pull'."


def git_checkout_source(
    source_type: str,
    source_name: Optional[str] = None,
    status_cb=None,
) -> tuple[bool, str]:
    """Safely move a Git checkout to a branch, PR, tag, or release.

    Existing local branches and commits are never reset. An existing local branch
    is reattached and fast-forwarded from the official repository; PRs, tags,
    releases, and branches without a local counterpart are checked out detached.
    A dirty working tree is refused before any checkout, and submodules are synced.
    """

    def log(msg):
        if status_cb:
            status_cb(msg)

    root = _git_repo_root()
    if root is None:
        return False, "Ankimon is not running from a git checkout."
    if shutil.which("git") is None:
        return False, "git wasn't found on Anki's PATH."

    def _git(*args, timeout=180):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        if source_type == "current":
            return git_pull_ff_only(status_cb=status_cb)

        status = _git("status", "--porcelain", timeout=30)
        if status.returncode != 0:
            return False, (status.stderr or status.stdout or "git status failed").strip()
        if status.stdout.strip():
            return False, (
                "Your Ankimon checkout has local changes. Commit, stash, or discard "
                "them before switching versions in the updater."
            )

        if not source_name:
            return False, "No Git target was selected."

        if source_type == "pr":
            remote_ref = f"refs/pull/{source_name}/head"
            display = f"PR #{source_name}"
        elif source_type == "branch":
            remote_ref = f"refs/heads/{source_name}"
            display = f"branch '{source_name}'"
        elif source_type in {"tag", "release"}:
            remote_ref = f"refs/tags/{source_name}"
            display = f"{source_type} '{source_name}'"
        else:
            return False, f"Unsupported Git source type: {source_type}"

        log(f"Fetching {display} from the Ankimon repository...")
        fetch = _git("fetch", "--force", GIT_REMOTE_URL, remote_ref)
        if fetch.returncode != 0:
            return False, (
                f"Could not fetch {display}.\n\n"
                + (fetch.stderr or fetch.stdout or "Unknown git fetch error").strip()
            )

        checkout_ref = "FETCH_HEAD"
        if source_type == "branch":
            local_branch = _git(
                "show-ref", "--verify", "--quiet", f"refs/heads/{source_name}",
                timeout=30,
            )
            if local_branch.returncode == 0:
                checkout = _git("checkout", source_name)
                if checkout.returncode != 0:
                    return False, (
                        f"Could not check out local branch '{source_name}'.\n\n"
                        + (checkout.stderr or checkout.stdout or "Unknown git checkout error").strip()
                    )
                log(f"Fast-forwarding local branch '{source_name}'...")
                merge = _git("merge", "--ff-only", checkout_ref)
                if merge.returncode != 0:
                    return False, (
                        f"Local branch '{source_name}' could not be fast-forwarded. "
                        "Your commits were left unchanged.\n\n"
                        + (merge.stderr or merge.stdout or "Unknown git merge error").strip()
                    )
                checkout_ref = None

        if checkout_ref is not None:
            log(f"Checking out {display}...")
            checkout = _git("checkout", "--detach", checkout_ref)
            if checkout.returncode != 0:
                return False, (
                    f"Could not check out {display}.\n\n"
                    + (checkout.stderr or checkout.stdout or "Unknown git checkout error").strip()
                )

        operation = "Updated" if checkout_ref is None else "Checked out"
        log("Updating submodules...")
        sub = _git("submodule", "update", "--init", "--recursive")
        if sub.returncode != 0:
            return True, (
                f"{operation} {display}, but the submodule update failed. Run "
                "'git submodule update --init --recursive' manually.\n\n"
                + (sub.stderr or sub.stdout or "").strip()
            )

        sha = _git("rev-parse", "--short", "HEAD", timeout=30)
        sha_text = sha.stdout.strip() if sha.returncode == 0 else "the selected commit"
        return True, (
            f"{operation} {display} at {sha_text}. Local commits were not rewritten. "
            "Please restart Anki."
        )
    except subprocess.TimeoutExpired:
        return False, "git timed out while switching the Ankimon checkout."
    except Exception as e:
        return False, f"git checkout failed: {e}"


def _parse_version(name: str) -> Optional[tuple]:
    """Coarse (major, minor) parse for the MIN_UPDATER_VERSION threshold gate ONLY.

    Returns None for non-version names ('sprites', 'nightly-release', 'archive/*').

    This is deliberately NOT a total ordering. It stops at minor and reads each
    component as an int, so '2.01' and '2.1' both parse to (2, 1) and a '2.0.1'
    patch is dropped. That is harmless for the `>= (2, 0)` threshold but WRONG for
    sorting or equality: do not pass it to sorted()/max() or use it to compare two
    releases. "Latest" is resolved by GitHub API order (newest first, see
    fetch_releases / update_dialog), never by this function.
    """
    m = re.match(r"v?(\d+)(?:\.(\d+))?", name.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 0))


def _is_supported_version(name: str) -> bool:
    """True for parseable version names >= MIN_UPDATER_VERSION. Non-version names
    (the 'sprites' asset release, 'nightly-release', 'archive/*', …) return False
    and are excluded from the pickers."""
    v = _parse_version(name)
    return v is not None and v >= MIN_UPDATER_VERSION


def _version_key(name: str) -> tuple:
    """Total-ordering key for this project's *decimal-minor* tag scheme, where the
    part after the major is a fraction — so 1.43 < 1.5, and 2.03 < 2.1. The "-E"
    channel suffix is stripped first (it selects a channel, not an order). Unlike
    ``_parse_version`` (a coarse threshold gate), this IS safe for max()/sorting.
    Non-version names sort lowest.

    NOTE: assumes single-dot decimal versions (2.03, 1.52, 1.3962). A multi-part
    semver like "2.0.1" won't match and sorts lowest — revisit if the scheme changes.
    """
    t = name.strip().lstrip("v")
    if t.endswith("-E"):
        t = t[:-2]
    m = re.match(r"(\d+)(?:\.(\d+))?$", t)
    if not m:
        return (-1, 0.0)
    major = int(m.group(1))
    frac = float("0." + m.group(2)) if m.group(2) else 0.0
    return (major, frac)


def channel_of_tag(tag: str) -> str:
    """Which release channel a tag belongs to: experimental if it ends in "-E",
    otherwise stable. (Main is a branch, never a tag, so it's not returned here.)"""
    return CHANNEL_EXPERIMENTAL if tag.strip().endswith("-E") else CHANNEL_STABLE


def is_newer_version(candidate: str, installed: str) -> bool:
    """True if ``candidate`` is strictly newer than ``installed`` under the
    decimal-minor scheme (channel suffix ignored). Used to decide whether to
    prompt: a same-or-older latest release never nags."""
    return _version_key(candidate) > _version_key(installed)


def latest_release_for_channel(channel: str) -> Optional[dict]:
    """Newest supported release (>= MIN_UPDATER_VERSION) on ``channel``.
    stable = tags without "-E", experimental = tags ending in "-E". Returns the
    release dict from ``fetch_releases`` (name/body/zipball_url) or None. "main"
    returns None — it's a branch, handled by the branch-SHA poll, not releases."""
    if channel == CHANNEL_MAIN:
        return None
    matches = [r for r in fetch_releases() if channel_of_tag(r["name"]) == channel]
    return max(matches, key=lambda r: _version_key(r["name"]), default=None)


def _get_settings():
    """The addon's settings service, or None if unavailable (headless tests,
    or the registry not yet populated)."""
    try:
        from ..services import services

        return services.settings
    except Exception:
        return None


def get_update_channel() -> str:
    """The user's selected auto-update channel. Defaults to matching the installed
    build — an "-E" build → experimental, otherwise stable — until the user picks
    one in the update dialog. Any unrecognized stored value falls back the same way."""
    settings = _get_settings()
    raw = None
    if settings is not None:
        try:
            raw = settings.get("misc.update_channel")
        except Exception:
            raw = None
    if raw in UPDATE_CHANNELS:
        return raw
    state = read_update_state()
    if state and state.get("source_type") == "branch":
        return CHANNEL_MAIN
    from ..resources import IS_EXPERIMENTAL_BUILD

    return CHANNEL_EXPERIMENTAL if IS_EXPERIMENTAL_BUILD else CHANNEL_STABLE


def set_update_channel(channel: str) -> None:
    """Persist the user's channel choice (ignored if not a known channel)."""
    if channel not in UPDATE_CHANNELS:
        return
    settings = _get_settings()
    if settings is not None:
        try:
            settings.set("misc.update_channel", channel)
        except Exception as e:
            print(f"Ankimon Updater: Failed to save update channel: {e}")


def _make_request(
    url: str, accept: str = "application/vnd.github.v3+json"
) -> urllib.request.Request:
    req = urllib.request.Request(url)
    req.add_header("Accept", accept)
    req.add_header("User-Agent", USER_AGENT)
    return req


def _api_get(endpoint: str) -> Optional[dict]:
    url = f"{GITHUB_API}/{endpoint}"
    req = _make_request(url)
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _fetch_gitignore_patterns() -> list[str]:
    url = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/main/.gitignore"
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as resp:
            lines = resp.read().decode().splitlines()
        patterns = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cleaned = line.replace("src/Ankimon/", "").strip("/")
            if cleaned:
                patterns.append(cleaned)
        return patterns
    except Exception:
        return []


def _should_preserve(rel_path: str, gitignore_patterns: list[str]) -> bool:
    # Holy Ground: NEVER touch anything in user_files/ during an update,
    # regardless of gitignore patterns.
    if rel_path == "user_files" or rel_path.startswith("user_files/"):
        return True

    # Unconditionally preserve local metadata, guides and changelogs
    always_preserve_roots = ["HelpInfos.html", "updateinfos.md", "meta.json"]
    if rel_path in always_preserve_roots:
        return True

    for pattern in gitignore_patterns:
        pattern = pattern.rstrip("/")
        if rel_path == pattern or rel_path.startswith(pattern + "/"):
            return True
        elif "*" in pattern:
            import fnmatch

            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
                os.path.basename(rel_path), pattern
            ):
                return True

    always_preserve = [
        "user_files/sprites/",
        "user_files/ankimon.db",
        "user_files/ankimonDEV.db",
        "user_files/update_state.json",
        "user_files/sprites_update_state.json",
        "user_files/sprites_local_manifest.json",
    ]
    for p in always_preserve:
        p = p.rstrip("/")
        if rel_path == p or rel_path.startswith(p + "/"):
            return True
    return False


def fetch_tags() -> list[dict]:
    data = _api_get("tags")
    if not data:
        return []
    return [
        {"name": t["name"], "zipball_url": t["zipball_url"]}
        for t in data
        if _is_supported_version(t["name"])
    ]


def fetch_releases() -> list[dict]:
    data = _api_get("releases")
    if not data:
        return []
    return [
        {
            "name": r["tag_name"],
            "body": r.get("body", ""),
            "zipball_url": r["zipball_url"],
            # When the release went public, which is the timestamp AnkiWeb's own
            # listing is compared against. Not the same as the tag's commit date.
            "published_at": r.get("published_at") or r.get("created_at"),
        }
        for r in data
        if _is_supported_version(r["tag_name"])
    ]


def fetch_branches() -> list[dict]:
    data = _api_get("branches")
    if not data:
        return []
    return [{"name": b["name"]} for b in data]


def fetch_open_prs() -> list[dict]:
    data = _api_get("pulls?state=open&per_page=50")
    if not data:
        return []
    return [
        {
            "number": pr["number"],
            "title": pr["title"],
            "head_ref": pr["head"]["ref"],
            "head_sha": pr["head"]["sha"],
        }
        for pr in data
    ]


def fetch_branch_sha(branch: str) -> Optional[str]:
    data = _api_get(f"branches/{branch}")
    if isinstance(data, dict) and isinstance(data.get("commit"), dict):
        return data["commit"].get("sha")
    return None


def fetch_ref_date(ref: str) -> Optional[str]:
    """Committer date (ISO 8601) of whatever ``ref`` resolves to — SHA or tag.

    Deliberately narrow about what it will put in a URL: the tags and branches
    the pickers offer are version-shaped, so anything stranger fails closed
    (no date, hence no timestamp stamped) rather than being sent to the API.
    """
    if not _is_safe_ref(ref):
        return None
    data = _api_get(f"commits/{ref}")
    commit = data.get("commit") if isinstance(data, dict) else None
    if isinstance(commit, dict):
        committer = commit.get("committer") or {}
        author = commit.get("author") or {}
        return committer.get("date") or author.get("date")
    return None


def fetch_commit_date(sha: str) -> Optional[str]:
    """Committer date (ISO 8601) of a commit SHA, or None.

    Narrower than ``fetch_ref_date``: the argument must look like a SHA (hex,
    at least the 7 characters GitHub's short form uses), so a branch or tag name
    is refused here even though the endpoint would accept it. Callers that hold
    a name rather than a SHA want ``fetch_ref_date``.
    """
    if not sha or len(sha) < 7 or not all(c in "0123456789abcdefABCDEF" for c in sha):
        return None
    return fetch_ref_date(sha)


def fetch_branch_commits(branch: str, local_sha: Optional[str] = None) -> list[dict]:
    def entry(c: dict) -> dict:
        # Commit messages can be empty — splitlines() on "" yields [].
        lines = (c["commit"]["message"] or "").splitlines()
        return {"sha": c["sha"][:7], "message": lines[0] if lines else ""}

    try:
        if (
            isinstance(local_sha, str)
            and len(local_sha) >= 7
            and all(c in "0123456789abcdefABCDEF" for c in local_sha)
        ):
            # Try to use the compare API
            data = _api_get(f"compare/{local_sha}...{branch}")
            if isinstance(data, dict) and isinstance(data.get("commits"), list):
                return [entry(c) for c in reversed(data["commits"])]

        # Fallback: get the last 5 commits of the branch
        data = _api_get(f"commits?sha={branch}&per_page=5")
        if isinstance(data, list):
            return [entry(c) for c in data]
    except Exception:
        pass
    return []


def get_update_state_path() -> Path:
    return addon_dir / "user_files" / "update_state.json"


def get_meta_json_path() -> Path:
    """Anki's metadata file for this add-on.

    Anki's, not ours — it also holds ``config``, ``disabled`` and the rest, so
    anything writing here edits only the key it owns. Indirected through a
    function so tests can point it at a temp directory.
    """
    return addon_dir / "meta.json"


def _write_json_atomic(path: Path, data: dict) -> None:
    """Write ``data`` as JSON via temp file + ``os.replace``.

    meta.json belongs to Anki, and a half-written file makes ``addonMeta()``
    fall back to an empty dict, silently discarding ``config``, ``disabled`` and
    ``mod``. Anki's own writer (``AddonManager.writeAddonMeta``) is a plain
    truncating ``open(..., "w")``, so a crash mid-write can do exactly that;
    replacing the file in one step means a reader sees either the old metadata
    or the new one.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise


def _parse_iso8601(value: Optional[str]) -> Optional[int]:
    """GitHub's ``2026-08-08T11:02:30Z`` as epoch seconds, or None."""
    if not value:
        return None
    try:
        from datetime import datetime, timezone

        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return int(parsed.replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return None


def resolve_build_mtime(
    source_type: Optional[str],
    source_name: Optional[str],
    commit_sha: Optional[str],
    published_at: Optional[str] = None,
) -> Optional[int]:
    """Epoch seconds describing the build about to be installed.

    A release — or a tag naming one — is dated by when that release went public,
    because that is the timestamp AnkiWeb's listing is compared against. The
    tag's own commit is typically minutes older, and under the AnkiWeb-first
    release order that gap is enough for the AnkiWeb upload to land in between,
    which would re-offer the identical version as an "update".

    Anything else is dated by the commit it was built from, and *that* value is
    capped at the present: a commit dated in the future (a skewed clock, or a
    crafted committer date on a PR build) would otherwise be written to ``mod``
    and, because the stamp never moves backwards, suppress AnkiWeb updates until
    that date arrived. The cap is deliberately not applied to ``published_at`` —
    that comes from GitHub's clock, not the committer's, so capping it against a
    slow local clock could only corrupt a value that was already correct.

    None means "could not tell", and the caller leaves ``mod`` alone.
    """
    import time

    if source_type in ("release", "tag"):
        published = _parse_iso8601(published_at)
        if published:
            return published
    # commit_sha and source_name are the same string on the release and tag
    # paths, so dedupe rather than repeat a request that already failed.
    for ref in dict.fromkeys(r for r in (commit_sha, source_name) if r):
        resolved = _parse_iso8601(fetch_ref_date(ref))
        if resolved:
            return min(resolved, int(time.time()))
    return None


def published_at_for_tag(tag: str, releases: list) -> Optional[str]:
    """The publish date of the release this tag names, if we already have it.

    Every tag the picker offers names a published release and installs
    byte-identical code, so a tag install should be dated exactly like the
    equivalent release install. Returns None when the tag names no release we
    know about, and the caller falls back to the tag's commit date.
    """
    if not tag or not releases:
        return None
    for release in releases:
        if isinstance(release, dict) and release.get("name") == tag:
            return release.get("published_at")
    return None


def stamp_addon_mod(timestamp: int) -> bool:
    """Record ``timestamp`` as meta.json's ``mod`` — the field Anki dates the
    installed build by. Returns whether anything was written.

    **Call this on Anki's main thread only.** meta.json is Anki's file, not
    ours: ``writeConfig``, ``toggleEnabled`` and ``write_addon_meta`` all
    read-modify-write the whole dict, and all of them run on the main thread.
    This function does the same read-modify-write, so running it from the
    updater worker would race them — the worker's snapshot, taken before a
    concurrent ``config`` write, would be replaced over the top of that write
    and silently revert it. Atomic replacement prevents a *torn* file; it does
    nothing about a *stale* one. Being on the main thread is what makes the
    read and the write one indivisible step relative to Anki's own. That is why
    ``apply_update`` resolves the timestamp in the worker but hands it back for
    the ``QueryOp`` success callback to stamp.

    Anki's ``AddonManager.write_addon_meta`` is the obvious alternative and is
    deliberately not used: it re-derives ``disabled``, ``conflicts``,
    ``min_point_version``, ``max_point_version``, ``branch_index`` and
    ``update_enabled`` from an ``AddonMeta`` dataclass, so it rewrites six
    fields we have no business touching, and it writes non-atomically. Editing
    the single key we own, atomically, on the same thread Anki writes from is
    strictly narrower.

    Anki decides an add-on is out of date with ``installed_at >= server_mtime``,
    where ``installed_at`` is this key (``AddonMeta.is_latest``). Only
    ``AddonManager.install()`` ever writes it, and the in-app updater bypasses
    that machinery entirely — so left alone the value keeps describing whichever
    build Anki last installed, and AnkiWeb's copy looks newer than a GitHub
    build that is in fact ahead of it. That is the accidental-downgrade prompt.

    Never moves ``mod`` backwards. Note what that does and does not buy: since
    Anki compares ``installed_at >= server_mtime``, a *lower* ``mod`` is what
    surfaces an AnkiWeb build, so refusing to lower it means a deliberate
    downgrade keeps the build the user chose instead of having AnkiWeb's silently
    offered back over the top. It also makes the stamp monotonic, so introducing
    it can never leave a user worse off than the un-stamped behaviour they have
    today. A meta.json that Anki did not create is left alone, as is one that no
    longer parses as a JSON object.
    """
    try:
        if not timestamp or timestamp <= 0:
            return False
        path = get_meta_json_path()
        if not path.exists():
            # No meta.json means Anki is not managing this install (git clone,
            # hand-unzipped copy); inventing one would fabricate metadata.
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(data, dict):
            return False
        current = data.get("mod")
        if isinstance(current, (int, float)) and timestamp <= current:
            return False
        data["mod"] = int(timestamp)
        _write_json_atomic(path, data)
        return True
    except Exception as e:
        print(f"Ankimon Updater: Failed to stamp meta.json mod: {e}")
        return False


def save_update_state(
    source_type: str,
    source_name: str,
    commit_sha: str,
    skip_until: Optional[float] = None,
):
    try:
        import time
        from ..resources import addon_ver

        path = get_update_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        old_skip = None
        if skip_until is None and path.exists():
            try:
                old_state = json.loads(path.read_text(encoding="utf-8"))
                old_skip = old_state.get("skip_until")
            except Exception:
                pass

        state = {
            "source_type": source_type,
            "source_name": source_name,
            "commit_sha": commit_sha,
            "installed_at": time.time(),
            "addon_version": addon_ver,
        }
        if skip_until is not None:
            state["skip_until"] = skip_until
        elif old_skip is not None:
            state["skip_until"] = old_skip

        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Ankimon Updater: Failed to save update state: {e}")


def set_update_skip_until(skip_until: float):
    try:
        state = read_update_state() or {}
        state["skip_until"] = skip_until
        path = get_update_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Ankimon Updater: Failed to set skip_until: {e}")


def read_update_state() -> Optional[dict]:
    try:
        path = get_update_state_path()
        if path.exists():
            # update_state.json lives in user_files/ and is user-editable, so
            # anything a text editor can produce must come back as None here.
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return None


def _download_zip_to_temp(url: str, progress_cb=None) -> Optional[str]:
    req = _make_request(url)
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0))

            # Create a named temporary file that persists after closing the object
            # but is cleaned up by our manual logic later.
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            tmp_path = tmp.name

            try:
                downloaded = 0
                chunk_size = 128 * 1024  # 128KB chunks
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    tmp.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total > 0:
                        progress_cb(downloaded, total)
                tmp.close()
                return tmp_path
            except Exception:
                tmp.close()
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise
    except Exception:
        return None


def _download_branch_zip(branch: str, progress_cb=None) -> Optional[str]:
    url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/archive/refs/heads/{branch}.zip"
    return _download_zip_to_temp(url, progress_cb)


def _download_pr_zip(head_sha: str, progress_cb=None) -> Optional[str]:
    url = f"{GITHUB_API}/zipball/{head_sha}"
    return _download_zip_to_temp(url, progress_cb)


def _get_gitignore_patterns() -> list[str]:
    patterns = _fetch_gitignore_patterns()
    if not patterns:
        patterns = [
            "user_files/mypokemon.json",
            "user_files/mainpokemon.json",
            "user_files/badges.json",
            "user_files/items.json",
            "user_files/data.json",
            "user_files/team.json",
            "user_files/config.obf",
            "user_files/pokemon_history.json",
            "user_files/rate_this.json",
            "user_files/backups",
            "user_files/todays_shop.json",
            "user_files/meta.json",
            "user_files/download_complete.flag",
            "user_files/ankimon.db",
            "user_files/json/*",
            "user_files/sprites/",
            "user_files/sprites_update_state.json",
            "user_files/sprites_local_manifest.json",
            "meta.json",
            "*.pyc",
            "*.log",
        ]
    return patterns


def _find_src_prefix(names: list[str]) -> Optional[str]:
    for name in names:
        if name.endswith("src/Ankimon/"):
            return name
    for name in names:
        if "src/Ankimon/__init__.py" in name:
            return name.rsplit("src/Ankimon/__init__.py", 1)[0] + "src/Ankimon/"
    return None


def _collect_code_files(gitignore_patterns: list[str]) -> dict[str, Path]:
    code_files = {}
    for root, dirs, files in os.walk(addon_dir):
        for fname in files:
            full_path = Path(root) / fname
            rel = str(full_path.relative_to(addon_dir)).replace("\\", "/")
            if not _should_preserve(rel, gitignore_patterns):
                code_files[rel] = full_path
    return code_files


def _extract_ref_from_prefix(src_prefix: str) -> str:
    # src_prefix is e.g. "ankimon-main/src/Ankimon/" or "h0tp-ftw-ankimon-a1b2c3d/src/Ankimon/"
    parts = src_prefix.strip("/").replace("\\", "/").split("/")
    if not parts:
        return "main"
    root_dir = parts[0]
    # Remove repo name prefix if present
    if root_dir.startswith("ankimon-"):
        ref = root_dir[len("ankimon-") :]
        return ref if ref else "main"
    elif "ankimon-" in root_dir:
        # e.g. h0tp-ftw-ankimon-a1b2c3d -> a1b2c3d
        ref = root_dir.split("ankimon-")[-1]
        return ref if ref else "main"
    return "main"


def _fetch_submodule_sha(ref: str) -> Optional[str]:
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/src/Ankimon/poke_engine?ref={ref}"
    req = _make_request(url)
    try:
        with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
            if isinstance(data, dict) and data.get("type") == "submodule":
                return data.get("sha")
    except Exception:
        pass
    return None


def _download_and_extract_submodule(sha: str, dest_dir: Path, status_cb=None):
    def log(msg):
        if status_cb:
            status_cb(msg)

    url = f"https://github.com/ArdentRoe/poke-engine/archive/{sha}.zip"
    log("Downloading poke_engine submodule package...")

    zip_path = _download_zip_to_temp(url)
    if not zip_path:
        raise Exception("Failed to download poke_engine submodule zip archive.")

    log("Extracting poke_engine submodule...")
    temp_extract_dir = Path(tempfile.mkdtemp(prefix="ankimon_submodule_extract_"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if not names:
                raise Exception("poke_engine submodule archive is empty.")
            # The root directory in zip is e.g. "poke-engine-{sha}"
            root_prefix = names[0].split("/")[0] + "/"

            for name in names:
                if not name.startswith(root_prefix) or name == root_prefix:
                    continue
                rel_path = name[len(root_prefix) :]
                if not rel_path or rel_path.endswith("/"):
                    continue

                dest_file = temp_extract_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as source, dest_file.open("wb") as target:
                    shutil.copyfileobj(source, target)

        # Atomic swap: Delete old dest_dir if it exists, rename temp_extract_dir to dest_dir
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        shutil.move(str(temp_extract_dir), str(dest_dir))
    except Exception as e:
        if temp_extract_dir.exists():
            try:
                shutil.rmtree(temp_extract_dir)
            except Exception:
                pass
        raise e
    finally:
        if os.path.exists(zip_path):
            try:
                os.unlink(zip_path)
            except Exception:
                pass


def apply_update(
    zip_path: str,
    source_type: Optional[str] = None,
    source_name: Optional[str] = None,
    commit_sha: Optional[str] = None,
    published_at: Optional[str] = None,
    status_cb=None,
) -> tuple[bool, str, Optional[int]]:
    """Install the downloaded build. Runs in the updater's ``QueryOp`` worker.

    Returns ``(ok, message, pending_mod)``. ``pending_mod`` is the epoch second
    the install should be dated by, for the caller to pass to
    ``stamp_addon_mod`` *from the main thread* — see that function for why the
    write cannot happen here. It is None whenever nothing should be stamped:
    a failed or rolled-back install, or a build date that could not be
    resolved.
    """

    def log(msg):
        if status_cb:
            status_cb(msg)

    def cleanup():
        if os.path.exists(zip_path):
            try:
                os.unlink(zip_path)
            except Exception:
                pass

    # Safety guard: this overwrites every file under addon_dir (the addon =
    # src/Ankimon) with the downloaded copy. On a git clone that trashes the
    # working tree and any uncommitted/untracked changes, so refuse. (.git lives
    # above addon_dir and is untouched, but the checkout is still clobbered.)
    if is_git_clone():
        cleanup()
        return (
            False,
            (
                "Detected a git checkout of Ankimon. The in-app updater overwrites "
                "the addon's files in place and would clobber your working tree "
                "(you'd lose uncommitted changes). Update your clone with 'git pull' "
                "instead."
            ),
            None,
        )

    log("Fetching latest .gitignore from main...")
    gitignore_patterns = _get_gitignore_patterns()

    log("Validating update archive...")
    backup_dir = None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            if not names:
                return False, "ZIP archive is empty.", None

            src_prefix = _find_src_prefix(names)
            if not src_prefix:
                return False, "Could not find src/Ankimon/ in the archive.", None

            new_files = {}
            for name in names:
                if not name.startswith(src_prefix) or name == src_prefix:
                    continue
                rel_path = name[len(src_prefix) :]
                if not rel_path or rel_path.endswith("/"):
                    continue
                if _should_preserve(rel_path, gitignore_patterns):
                    continue
                new_files[rel_path] = name

            if not new_files:
                return False, "No addon files found in the archive.", None

            log(f"Archive validated: {len(new_files)} files to install.")

            # --- Backup current code files ---
            log("Backing up current addon code...")
            backup_dir = Path(tempfile.mkdtemp(prefix="ankimon_update_backup_"))
            code_files = _collect_code_files(gitignore_patterns)
            backed_up = 0
            for rel, full_path in code_files.items():
                dest = backup_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(full_path, dest)
                    backed_up += 1
                except Exception:
                    pass
            log(f"Backed up {backed_up} code files to {backup_dir.name}.")

            # --- Apply update ---
            log("Removing old addon code...")
            for rel, full_path in code_files.items():
                try:
                    full_path.unlink()
                except Exception:
                    pass

            for root, dirs, _ in os.walk(addon_dir, topdown=False):
                for dname in dirs:
                    dir_path = Path(root) / dname
                    try:
                        if not any(dir_path.iterdir()):
                            dir_path.rmdir()
                    except Exception:
                        pass

            log("Installing new files...")
            installed = 0
            for rel_path, zip_name in new_files.items():
                if ".." in rel_path or os.path.isabs(rel_path):
                    continue
                dest = addon_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with zf.open(zip_name) as source, dest.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    installed += 1
                except PermissionError as pe:
                    if dest.exists():
                        log(
                            f"Warning: Skipping locked file {rel_path} (already exists)"
                        )
                    else:
                        raise pe

            # --- Download and install matching poke_engine submodule version ---
            ref = _extract_ref_from_prefix(src_prefix)
            log(f"Resolving poke_engine submodule for ref '{ref}'...")
            sub_sha = _fetch_submodule_sha(ref) or DEFAULT_SUBMODULE_SHA

            _download_and_extract_submodule(
                sub_sha, addon_dir / "poke_engine", status_cb
            )

            # --- Save update state if provided ---
            if source_type and source_name:
                save_update_state(source_type, source_name, commit_sha or "")

            cleanup()
            log(f"Update complete. Installed {installed} files.")

            # Cleanup backup on success
            try:
                shutil.rmtree(backup_dir)
            except Exception:
                pass

            # Work out the date to stamp meta.json with, so Anki stops treating
            # whichever build it last installed as the newer copy. Resolving it
            # needs the network, which is why it happens here in the worker; the
            # write itself does not happen here at all — it is handed back for
            # the caller's main-thread success callback, because meta.json is
            # Anki's file and Anki read-modify-writes it from the main thread.
            # See stamp_addon_mod for the full argument.
            #
            # Deliberately resolved last, after every statement that could still
            # raise into the rollback handler below, and returned only on this
            # success path. A rolled-back install must report None: stamping a
            # restored old build as the new one would make Anki believe the
            # rollback is current and suppress the very update that repairs it.
            # The lookup is guarded separately so a GitHub hiccup cannot fail an
            # install that has already finished.
            pending_mod = None
            try:
                pending_mod = resolve_build_mtime(
                    source_type, source_name, commit_sha, published_at
                )
            except Exception as e:
                print(f"Ankimon Updater: Could not date the install: {e}")

            return (
                True,
                "Update applied successfully. Please restart Anki.",
                pending_mod,
            )

    except Exception as e:
        # --- Rollback ---
        log(f"Update failed: {e}. Rolling back...")
        rollback_count = 0
        if backup_dir is not None:
            for root, dirs, files in os.walk(backup_dir):
                for fname in files:
                    backup_path = Path(root) / fname
                    rel = str(backup_path.relative_to(backup_dir)).replace("\\", "/")
                    dest = addon_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(backup_path, dest)
                        rollback_count += 1
                    except Exception:
                        pass
            log(f"Rolled back {rollback_count} files.")

            try:
                shutil.rmtree(backup_dir)
            except Exception:
                pass

        cleanup()
        return False, f"Update failed and was rolled back: {e}", None
