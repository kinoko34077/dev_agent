# Critical Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the audited P0 external-worker, billing, dependency, and verification-integrity risks without adding a scheduler, database, or agent framework.
**Architecture:** Preserve the existing DevFarm/Host Verification, QualificationResolver, ResourceControlPlane, Planner, and durable artifact boundaries. Add fail-closed gates and immutable evidence at their current boundaries; keep OS sandbox capability explicit and unavailable until actually provided.
**Tech Stack:** Python 3.10/3.11, pytest, SQLite, pathlib, subprocess, existing DevFarm scripts, existing qualification/billing/state APIs.
**Spec:** `docs/CURRENT_STATE.md`, `docs/DEVFARM.md`, `spec/v2/`, and the user-provided critical-hardening audit.

## Global Constraints

- Preserve external task/provider/budget/quota/approval/lease/UNKNOWN semantics.
- Do not add a parallel scheduler, state store, retry framework, or agent framework.
- External providers default to `STATIC_ONLY`; never describe host containment as an OS sandbox.
- Use canonical qualification and billing authorities; do not trust model self-report or `cost_minor == 0` alone.
- Keep changes in focused commits, with focused tests before implementation and full `tests/v2` before delivery.

---

## 1. Baseline and audit reproductions

- [ ] Confirm branch, HEAD, clean/dirty status, Gate status, and current test evidence.
- [ ] Add failing regressions for static-only external verification and dangerous test-command options.
- [ ] Add failing regressions for canonical qualification admission, recurring allowance billing, failed dependency persistence, and verification artifact mutation.

## 2. P0 external execution boundary

- [ ] Add explicit verification trust levels and attempt-scoped approval representation.
- [ ] Make external-provider verification default to `STATIC_ONLY`; reject execution and acceptance without `OS_SANDBOXED` or explicit trusted approval.
- [ ] Introduce strict structured/normalized host test command validation and sanitized pytest environment.
- [ ] Add resource/output limits before provider calls and worktree creation.

## 3. P0 qualification and billing authority

- [ ] Route DevFarm qualification through the canonical indexed `QualificationResolver` with exact binding identity and high/current admission.
- [ ] Add overage-aware billing admission; only `free_fixed` or an explicit trusted no-charge allowance can settle an omitted cost as zero.
- [ ] Keep qualification, billing, activation, and privacy as separate authorities.

## 4. P0 durable state and verification evidence

- [ ] Commit failed/cancelled planner dependency propagation durably and prevent artifact metadata from releasing failed dependencies.
- [ ] Bind proposal, manifest, test specification, containment, verified tests, and patch bytes through SHA-256 verification artifacts.
- [ ] Make attempt artifacts append-only/exclusive and make integration verify the durable verification record rather than caller-provided metadata.

## 5. P1 protection and evidence hygiene

- [ ] Expand protected authority/supply-chain/secret path policy and keep it shared by manifest and patch validation.
- [ ] Extend resource repair and legacy validation to billing authority metadata without provider-name hardcoding.
- [ ] Sanitize host output and require verification evidence outside Worker-owned changes before acceptance.

## 6. Verification and synchronization

- [ ] Run focused affected tests after each slice.
- [ ] Run architecture checks and full `tests/v2` regression.
- [ ] Run exact-head/read-only Gate checks; do not change G6O1 or external GitHub protection claims.
- [ ] Synchronize Current State, DevFarm, Traceability, and relevant specs with only evidenced implementation changes.
- [ ] Commit each coherent slice and push only after the full local gate is green.

## Completion evidence

- [ ] External Worker code is never automatically executed on an unsandboxed host.
- [ ] Qualification, billing, dependency, and verification-integrity regressions are green.
- [ ] Full v2 regression and architecture checks are green.
- [ ] Any unavailable OS sandbox or GitHub protection remains explicitly external/blocked rather than implied complete.
