#!/usr/bin/env python3
"""
run_proof_scenario.py — Executes a targeted Tier-1 or Tier-2 proof script.

Usage:
  python run_proof_scenario.py --file <PATH_TO_PROOF_SCRIPT>
  python run_proof_scenario.py --smoke
"""

import argparse
import importlib.util
import os
import sys
import traceback

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def run_smoke_tier1():
    """Runs standard smoke check using Driver."""
    print("Running Tier 1 Smoke Play-Through Proof...")
    from harness.driver import Driver
    d = Driver(seed={"main": {"species": "Pikachu", "level": 20}})
    d.set_enemy(species="Rattata", level=5)
    d.answer("good")
    events = d.drain_events()
    errors = [e for e in events if e.get("type") == "error"]
    if errors:
        print(f"❌ FAILED: Error events encountered: {errors}")
        return False
    print("✅ PASSED: Tier 1 Smoke Play verified cleanly.")
    return True


def run_proof_file(file_path):
    """Executes a user-specified Python proof script."""
    print(f"Executing Proof Scenario: {file_path}...")
    abs_path = os.path.abspath(file_path)
    if not os.path.exists(abs_path):
        print(f"❌ File not found: {abs_path}")
        return False

    spec = importlib.util.spec_from_file_location("proof_module", abs_path)
    if not spec or not spec.loader:
        print(f"❌ Could not load module spec for {abs_path}")
        return False

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        if hasattr(module, "run_proof"):
            res = module.run_proof()
            if res is False:
                print("❌ Proof returned False (assertion failed)")
                return False
        print(f"✅ PASSED: Proof scenario '{os.path.basename(file_path)}' completed successfully.")
        return True
    except Exception as e:
        print(f"❌ FAILED: Exception occurred during proof execution:\n")
        traceback.print_exc()
        return False


def scaffold_test(tier, target_path):
    """Scaffolds a new proof test file from templates."""
    template_name = "tier2_proof_template.py" if tier == 2 else "tier1_proof_template.py"
    template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", template_name)
    
    if not os.path.exists(template_path):
        print(f"❌ Template not found at: {template_path}")
        return False
        
    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(content)
        
    print(f"✅ Created new Tier-{tier} proof scaffold at: {target_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Run or scaffold focused Tier 1 / Tier 2 proof scenarios")
    parser.add_argument("--file", help="Path to custom proof Python file to execute")
    parser.add_argument("--smoke", action="store_true", help="Run quick Tier-1 smoke proof")
    parser.add_argument("--init-tier1", help="Create new Tier 1 proof script scaffold at target path")
    parser.add_argument("--init-tier2", help="Create new Tier 2 proof script scaffold at target path")

    args = parser.parse_args()

    if args.init_tier1:
        success = scaffold_test(1, args.init_tier1)
    elif args.init_tier2:
        success = scaffold_test(2, args.init_tier2)
    elif args.smoke:
        success = run_smoke_tier1()
    elif args.file:
        success = run_proof_file(args.file)
    else:
        print("Please provide --file <PATH>, --smoke, --init-tier1 <PATH>, or --init-tier2 <PATH>")
        sys.exit(1)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
