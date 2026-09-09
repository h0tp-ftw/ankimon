## 📋 PR Review: {{PR_TITLE}} (#{{PR_NUMBER}})

### 🔍 Executive Summary & Impact Assessment
- **Reviewed Head SHA**: {{HEAD_SHA}}
- **Summary**: {{PR_SUMMARY}}
- **Risk Level**: {{RISK_LEVEL}} *(Low / Medium / High / Critical)*
- **Affected Subsystems**: {{AFFECTED_SUBSYSTEMS}}

---

### 🧪 Focused Proof & Verification Results
- [ ] **Baseline Tier-1 Gate**: `python harness/check.py` — **NOT RUN**; {{STATUS_AND_EVIDENCE}}.
- [ ] **Module Integrity**: `pytest tests/test_addon_integrity.py` — **NOT RUN**; {{STATUS_AND_EVIDENCE}}.
- [ ] **Targeted Tier-1 / Tier-2 Proof** — **NOT RUN**:
  - **Scenario**: {{PROOF_SCENARIO_DESCRIPTION}}
  - **Command and Exit Code**: {{COMMAND_AND_EXIT_CODE}}
  - **Proof Result and Output**: {{PROOF_OUTCOME_AND_ACTUAL_OUTPUT}}
  - **Untested Behavior / Blocked Checks**: {{VALIDATION_LIMITS}}

---

### 🚨 Critical / Blocker Issues (Must Fix Before Merge)
*(If none, state "None detected.")*

- **{{ISSUE_TITLE}}**
  - **Location**: [`{{FILE_PATH}}:L{{START_LINE}}-L{{END_LINE}}`]({{SOURCE_URL}})
  - **Root Cause**: {{ROOT_CAUSE_DESCRIPTION}}
  - **Failure Mode**: {{FAILURE_MODE}}
  - **Suggested Fix**:
    ```diff
    - {{OLD_CODE}}
    + {{FIXED_CODE}}
    ```

---

### 💡 Suggestions & Polish (Non-Blocking)
- **{{SUGGESTION_TITLE}}**
  - **Location**: [`{{FILE_PATH}}:L{{LINE_NUMBER}}`]({{SOURCE_URL}})
  - **Note**: {{SUGGESTION_DETAILS}}

---

### 🏁 Final Verdict
- [ ] **Approve (Ready to Merge)**
- [ ] **Approve with Minor Suggestions**
- [ ] **Request Changes (Blockers Identified)**
