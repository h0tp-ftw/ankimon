# Comprehensive Code Review Checklist

Use this checklist during PR reviews to systematically evaluate code changes across 6 critical dimensions.

---

## 1. Functional Correctness & Edge Cases
- [ ] **Boundary Conditions**: Are edge inputs handled properly (e.g., empty lists, `None`/`null`, 0, negative integers, huge numbers)?
- [ ] **State Invariants**: Does the state transition maintain valid invariants (e.g. `0 <= hp <= max_hp`, positive level, valid move count `1 <= moves <= 4`)?
- [ ] **Exception Handling**: Are exceptions caught specifically rather than with bare `except:`? Are exceptions logged or reported through `services.logger` or `events.py`?
- [ ] **Null / None Checks**: Are optional dictionary lookups guarded with `.get(key, default)` or appropriate `None` checks?
- [ ] **Off-by-One Errors**: Are range calculations, array slices, and loop indices exact?

---

## 2. Architecture, Decoupling & Invariants
- [ ] **Headless Seam**: Are all core/business logic files free from `aqt` and `PyQt6` top-level imports?
- [ ] **Port / Presenter Routing**: Are user alerts or prompts routed through `services.ui` rather than direct Qt dialog instantiation?
- [ ] **Dependency Hierarchy**: Do dependencies flow in one direction (e.g., UI depends on Core, Core does not depend on UI)?
- [ ] **Data Model Consistency**: Do new columns, attributes, or settings include sensible defaults for legacy records?

---

## 3. Performance & Resource Management
- [ ] **Synchronous I/O on Hot Paths**: Are card answer hooks and battle loops free of synchronous file reads, network calls, or expensive regexes?
- [ ] **Database Query Efficiency**: Are queries avoiding full-table scans inside iterative loops (N+1 query problem)?
- [ ] **Memory & Object Lifecycles**: Are event subscriptions, timers, or background workers cleanly disposed of to prevent memory leaks?
- [ ] **WebEngine Resource Hygiene**: Are WebChannel objects properly registered and unmounted when tabs close?

---

## 4. Security & Safety
- [ ] **SQL Injection**: Are all database queries parameterized (using `?` placeholders) rather than formatted with f-strings?
- [ ] **Webview XSS**: Is data sent across WebChannel / QWebEngine bridges sanitized before being rendered into innerHTML?
- [ ] **Input Sanitization**: Are user-supplied names, nicknames, and paths validated against invalid filesystem or injection characters?
- [ ] **Credentials / Secrets**: Are no development tokens, passwords, or personal paths hardcoded in the diff?

---

## 5. Testing & Verification Evidence
- [ ] **Baseline Passes**: Does `python harness/check.py` pass without errors?
- [ ] **Test Quality**: Do tests verify behavioral outcomes (state mutations, event emissions) rather than asserting mock implementation details?
- [ ] **Focused Proof Scenario**: Has a targeted Tier-1 or Tier-2 proof been run to demonstrate that the claimed improvement/fix actually works?

---

## 6. Maintainability & Code Health
- [ ] **Clarity & Naming**: Are variables, functions, and classes named descriptively?
- [ ] **Documentation**: Are complex algorithms or non-obvious design decisions explained in comments?
- [ ] **Dead Code**: Is deprecated or commented-out code removed rather than left in place?
