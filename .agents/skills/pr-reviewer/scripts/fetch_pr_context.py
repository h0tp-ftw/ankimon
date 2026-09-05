#!/usr/bin/env python3
"""
fetch_pr_context.py — Extracts PR metadata, diffs, and blast radius classification.

Usage:
  python fetch_pr_context.py --pr <PR_NUMBER>
  python fetch_pr_context.py --local [--base main]
"""

import argparse
import json
import os
import subprocess
import sys

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SUBSYSTEM_MAP = {
    "src/Ankimon/database_manager.py": ("Database", "Critical"),
    "src/Ankimon/core.py": ("Core Decoupling", "High"),
    "src/Ankimon/battle_loop.py": ("Battle Engine", "High"),
    "src/Ankimon/functions/encounter_functions.py": ("Encounter Economy", "High"),
    "src/Ankimon/functions/ankimon_hooks_to_poke_engine.py": ("Poke-Engine Bridge", "High"),
    "src/Ankimon/webshell/": ("WebShell Host", "Medium"),
    "src/Ankimon/ankidex/": ("Ankidex UI", "Medium"),
    "src/Ankimon/pyobj/": ("Qt Windows", "Medium"),
    "src/Ankimon/gui_classes/": ("GUI Classes", "Medium"),
    "harness/": ("Headless Harness", "Low"),
    "tests/": ("Unit Tests", "Low"),
}


def run_cmd(cmd):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error running '{cmd}': {e.stderr.strip()}", file=sys.stderr)
        return None


def get_pr_info_gh(pr_num):
    cmd = f"gh pr view {pr_num} --json number,title,body,author,baseRefName,headRefName,url"
    out = run_cmd(cmd)
    if not out:
        return None
    return json.loads(out)


def get_pr_diff(pr_num=None, local=False, base="main"):
    if local:
        return run_cmd(f"git diff origin/{base}...HEAD")
    else:
        return run_cmd(f"gh pr diff {pr_num}")


def get_changed_files(pr_num=None, local=False, base="main"):
    if local:
        out = run_cmd(f"git diff --numstat origin/{base}...HEAD")
    else:
        out = run_cmd(f"gh pr diff {pr_num} --name-only")
        if out:
            # Get numstat if possible
            stat_out = run_cmd(f"gh pr diff {pr_num} --numstat")
            if stat_out:
                out = stat_out
    
    files = []
    if not out:
        return files
        
    for line in out.strip().splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) == 3:
            adds, dels, path = parts
            files.append({"path": path, "adds": adds, "dels": dels})
        else:
            files.append({"path": line.strip(), "adds": "?", "dels": "?"})
    return files


def classify_risk(files):
    risk = "Low"
    subsystems = set()
    
    for f in files:
        p = f["path"].replace("\\", "/")
        matched = False
        for prefix, (subsys, r_level) in SUBSYSTEM_MAP.items():
            if p.startswith(prefix) or prefix in p:
                subsystems.add(subsys)
                if r_level == "Critical":
                    risk = "Critical"
                elif r_level == "High" and risk != "Critical":
                    risk = "High"
                elif r_level == "Medium" and risk not in ("Critical", "High"):
                    risk = "Medium"
                matched = True
        if not matched:
            subsystems.add("General / Other")
            
    return risk, sorted(list(subsystems))


def main():
    parser = argparse.ArgumentParser(description="PR Context & Blast Radius Analyzer")
    parser.add_argument("--pr", type=int, help="GitHub PR number")
    parser.add_argument("--local", action="store_true", help="Inspect local branch diff against base")
    parser.add_argument("--base", default="main", help="Base branch for local diff (default: main)")
    
    args = parser.parse_args()
    
    if not args.pr and not args.local:
        print("Please specify either --pr <NUMBER> or --local")
        sys.exit(1)
        
    print("=" * 60)
    print("🔍 PR CONTEXT & BLAST RADIUS REPORT")
    print("=" * 60)
    
    if args.pr:
        info = get_pr_info_gh(args.pr)
        if info:
            print(f"PR Title:   #{info.get('number')} {info.get('title')}")
            print(f"Author:     {info.get('author', {}).get('login', 'unknown')}")
            print(f"Branches:   {info.get('headRefName')} -> {info.get('baseRefName')}")
            print(f"URL:        {info.get('url')}")
            print("-" * 60)
    
    files = get_changed_files(pr_num=args.pr, local=args.local, base=args.base)
    risk, subsystems = classify_risk(files)
    
    print(f"Overall Risk Assessment:  [{risk.upper()}]")
    print(f"Affected Subsystems:      {', '.join(subsystems)}")
    print("-" * 60)
    print(f"Changed Files ({len(files)}):")
    for f in files:
        print(f"  • {f['path']} (+{f['adds']} / -{f['dels']})")
    print("=" * 60)


if __name__ == "__main__":
    main()
