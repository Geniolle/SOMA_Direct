# SOMA Watchdog Pending Start Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start one SOMA monthly audit only when CONTAORDEM contains eligible work and no execution or uncertain result blocks it.

**Architecture:** Extend the existing five-minute watchdog. Reuse `SomaAuditBatchOrchestrator.available_periods()` for the read-only pending check and the existing tmux supervisor command for launch.

**Tech Stack:** Python 3.12+, pytest, systemd timer, tmux, Google Sheets service.

**Spec:** User-approved design in this conversation.

## Global Constraints

- Do not change the central `appextrato` runtime or its `opc` permissions.
- Never start a second supervisor.
- Never start while any checkpoint has uncertain financial results.
- A Sheets/authentication failure must log and stop without launching.
- Reuse existing credentials and project environment.

## Review Focus

- Completed run with no eligible periods stays idle.
- Completed run with eligible periods starts exactly once.
- Running process blocks launch even when work exists.
- Uncertain result blocks launch.
- Pending-check failure blocks launch and remains observable in the watchdog log.

---

### Task 1: Pending-aware watchdog

**Files:**
- Modify: `workflows/soma_audit_watchdog.py`
- Test: `tests/test_soma_audit_watchdog.py`

**Interfaces:**
- Consumes: `SomaAuditBatchOrchestrator.available_periods() -> dict[str, int]`
- Produces: `should_start(process_running, state, uncertain_results, log_tail, pending_periods) -> bool`

- [x] Write failing tests for the five Review Focus cases.
- [x] Run the focused tests and confirm failure for missing pending-aware behavior.
- [x] Add the smallest pending discovery and start-decision change.
- [x] Run focused tests, full suite and Ruff critical checks.
- [x] Deploy the watchdog file and tests, run remote focused tests, restart the timer and verify one real tick.
