## 📋 PR Review: {{PR_TITLE}} (#{{PR_NUMBER}})

### 🔍 Executive Summary & Impact Assessment
- **Summary**: {{PR_SUMMARY}}
- **Risk Level**: {{RISK_LEVEL}} *(Low / Medium / High / Critical)*
- **Affected Subsystems**: {{AFFECTED_SUBSYSTEMS}}

---

### 🧪 Focused Proof & Verification Results
- [x] **Baseline Tier-1 Gate**: `python harness/check.py` — **PASSED** (0 failures).
- [x] **Module Integrity**: `pytest tests/test_addon_integrity.py` — **PASSED**.
- [x] **Targeted Tier-1 / Tier-2 Proof**:
  - **Scenario**: {{PROOF_SCENARIO_DESCRIPTION}}
  - **Proof Result**: {{PROOF_OUTCOME}}

---

### 🚨 Critical / Blocker Issues (Must Fix Before Merge)
*(If none, state "None detected.")*

- **{{ISSUE_TITLE}}**
  - **Location**: [`{{FILE_PATH}}:L{{START_LINE}}-L{{END_LINE}}`](file:///{{FILE_PATH}}#L{{START_LINE}}-L{{END_LINE}})
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
  - **Location**: [`{{FILE_PATH}}:L{{LINE_NUMBER}}`](file:///{{FILE_PATH}}#L{{LINE_NUMBER}})
  - **Note**: {{SUGGESTION_DETAILS}}

---

### 🏁 Final Verdict
- [ ] **Approve (Ready to Merge)**
- [ ] **Approve with Minor Suggestions**
- [ ] **Request Changes (Blockers Identified)**
