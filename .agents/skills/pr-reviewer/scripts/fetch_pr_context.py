#!/usr/bin/env python3
"""
fetch_pr_context.py — Extracts PR metadata, diffs, and blast radius classification.

Usage:
  python fetch_pr_context.py --pr <PR_NUMBER>
  python fetch_pr_context.py --local [--base main]
"""

import argparse
import json
import subprocess
import sys

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SUBSYSTEM_MAP = {
    "src/Ankimon/pyobj/database_manager.py": ("Database", "Critical"),
    "src/Ankimon/core.py": ("Core Decoupling", "High"),
    "src/Ankimon/battle_loop.py": ("Battle Engine", "High"),
    "src/Ankimon/functions/encounter_functions.py": ("Encounter Economy", "High"),
    "src/Ankimon/functions/ankimon_hooks_to_poke_engine.py": (
        "Poke-Engine Bridge",
        "High",
    ),
    "src/Ankimon/webshell/": ("WebShell Host", "Medium"),
    "src/Ankimon/ankidex/": ("Ankidex UI", "Medium"),
    "src/Ankimon/pyobj/": ("Qt Windows", "Medium"),
    "src/Ankimon/gui_classes/": ("GUI Classes", "Medium"),
    "harness/": ("Headless Harness", "Low"),
    "tests/": ("Unit Tests", "Low"),
}


def run_cmd(cmd):
    """Run an argument list, preserving an empty result and raising on failure."""
    try:
        res = subprocess.run(
            cmd, shell=False, capture_output=True, text=True, check=True
        )
        return res.stdout
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Command {cmd[0]!r} failed: {(e.stderr or '').strip()}"
        ) from e
    except OSError as e:
        raise RuntimeError(f"Could not run {cmd[0]!r}: {e}") from e


def get_pr_info_gh(pr_num):
    """Read PR metadata or propagate the CLI/JSON error to the caller."""
    return json.loads(
        run_cmd(
            [
                "gh",
                "pr",
                "view",
                str(pr_num),
                "--json",
                "number,title,body,author,baseRefName,headRefName,url",
            ]
        )
    )


def get_pr_diff(pr_num=None, local=False, base="main"):
    """Read a diff, treating the supplied base ref as a literal argument."""
    if local:
        return run_cmd(["git", "diff", f"origin/{base}...HEAD", "--"])
    else:
        return run_cmd(["gh", "pr", "diff", str(pr_num)])


def get_changed_files(pr_num=None, local=False, base="main"):
    """Read changed paths; an unavailable diff must never look like zero changes."""
    if local:
        out = run_cmd(
            [
                "git",
                "diff",
                "--numstat",
                "-z",
                "--no-renames",
                f"origin/{base}...HEAD",
                "--",
            ]
        )
        files = []
        for entry in out.split("\0"):
            if entry:
                adds, dels, path = entry.split("\t", 2)
                files.append({"path": path, "adds": adds, "dels": dels})
        return files

    # gh pr diff has no --numstat option. Keep each name-only line intact.
    out = run_cmd(["gh", "pr", "diff", str(pr_num), "--name-only"])
    return [
        {"path": path, "adds": "?", "dels": "?"} for path in out.splitlines() if path
    ]


def classify_risk(files):
    """Classify paths using specific files before broader directory rules."""
    risk = "Low"
    subsystems = set()

    for f in files:
        p = f["path"].replace("\\", "/")
        matched = False
        for prefix, (subsys, r_level) in SUBSYSTEM_MAP.items():
            if p == prefix or (prefix.endswith("/") and p.startswith(prefix)):
                subsystems.add(subsys)
                if r_level == "Critical":
                    risk = "Critical"
                elif r_level == "High" and risk != "Critical":
                    risk = "High"
                elif r_level == "Medium" and risk not in ("Critical", "High"):
                    risk = "Medium"
                matched = True
                break
        if not matched:
            subsystems.add("General / Other")

    return risk, sorted(list(subsystems))


def main():
    """Print a report only when all required context was retrieved."""
    parser = argparse.ArgumentParser(description="PR Context & Blast Radius Analyzer")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", type=int, help="GitHub PR number")
    source.add_argument(
        "--local", action="store_true", help="Inspect local branch diff against base"
    )
    parser.add_argument(
        "--base", default="main", help="Base branch for local diff (default: main)"
    )

    args = parser.parse_args()

    if args.pr is not None and args.pr < 1:
        parser.error("--pr must be a positive PR number")

    print("=" * 60)
    print("🔍 PR CONTEXT & BLAST RADIUS REPORT")
    print("=" * 60)

    try:
        info = get_pr_info_gh(args.pr) if args.pr else None
        files = get_changed_files(pr_num=args.pr, local=args.local, base=args.base)
    except (RuntimeError, ValueError) as e:
        print(f"Context retrieval failed: {e}", file=sys.stderr)
        return 1

    if info:
        print(f"PR Title:   #{info.get('number')} {info.get('title')}")
        print(f"Author:     {info.get('author', {}).get('login', 'unknown')}")
        print(f"Branches:   {info.get('headRefName')} -> {info.get('baseRefName')}")
        print(f"URL:        {info.get('url')}")
        print("-" * 60)

    risk, subsystems = classify_risk(files)

    print(f"Overall Risk Assessment:  [{risk.upper()}]")
    print(f"Affected Subsystems:      {', '.join(subsystems)}")
    print("-" * 60)
    print(f"Changed Files ({len(files)}):")
    for f in files:
        print(f"  • {f['path']} (+{f['adds']} / -{f['dels']})")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
