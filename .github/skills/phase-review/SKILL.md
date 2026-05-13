---
name: phase-review
description: 'Review a completed project phase against the execution plan. Use when user asks 阶段复盘, phase review, 阶段验收, 检查是否越界, 检查边界约束, 检查数据格式约定, milestone quality gate, post-phase audit.'
argument-hint: 'Describe the completed phase and where to verify changes'
user-invocable: true
---

# Phase Review

Use this skill to perform a strict post-phase quality gate after a phase implementation is finished.

Primary plan and status source:
- [项目修改计划](../phase-driven-implementation/references/项目修改计划.md)

Review rubric:
- [Phase Review Rubric](./references/review-rubric.md)

## What This Skill Produces

- A phase audit focused on scope boundary, architecture boundary, and data contract compliance
- A pass or fail decision for the finished phase
- A concise violation list with file evidence
- A status update recommendation for the plan status table

## When to Use

- A phase is reported as done and needs acceptance
- The user asks whether implementation crossed phase boundaries
- The user asks whether plan-defined constraints and formats were respected
- The team needs a release gate per milestone

Do not use this skill when:
- Work is still in-progress and no completion claim exists
- The user asks for brainstorming instead of verification

## Required Inputs

Before review, identify:
1. Target phase and expected scope in the plan
2. Claimed completed files and validations
3. Plan constraints for architecture boundaries and data formats
4. Current phase status row in the plan status table

If the phase is not explicitly named, infer the latest phase marked in-progress or recently completed in the plan status table.

## Review Workflow

### 1. Lock Scope Baseline

- Read target phase section and extract expected files, boundaries, and data contracts.
- Read current plan status row for the target phase.
- Build a checklist from [Phase Review Rubric](./references/review-rubric.md).

Output checklist:
- Target phase
- Expected scope files
- Expected boundary constraints
- Expected data format contracts

### 2. Verify Scope Boundary

- Confirm changed files belong to current phase or strict prerequisites only.
- Flag any later-phase implementation pulled in without explicit approval.
- Flag missing mandatory files defined by the phase.

### 3. Verify Architecture Boundary

- Confirm route layer does not contain business logic.
- Confirm service and repository responsibilities remain separated.
- Confirm async task wrappers do not become the only domain logic location.
- Confirm workflow engine is not coupled to transport layer.

### 4. Verify Data Contract Compliance

- Validate request and response payload shapes against plan agreements.
- Validate enum values and state transitions.
- Validate JSON fields such as Config, Attachments, Cited_Sources, vector id arrays, and websocket payloads.
- Validate error payload includes code, message, retryability, and retry timing when defined.

### 5. Verify Validation Evidence

- Confirm at least one focused executable validation was run.
- Confirm evidence matches touched scope.
- If no executable validation exists, require explicit blocker reason.

### 6. Produce Gate Decision

Use one of three outcomes:
- PASS: no material violations, phase can be marked completed
- CONDITIONAL PASS: minor issues, phase can proceed with explicit follow-up items
- FAIL: scope breach, boundary violation, or contract mismatch; phase remains in-progress or blocked

### 7. Update Plan Status Recommendation

- If PASS: recommend status update to completed with evidence and timestamp.
- If CONDITIONAL PASS: recommend completed with follow-up items and deadline.
- If FAIL: recommend blocked or in-progress with blocker reason.

## Hard Rules

1. Do not approve if there is a confirmed scope breach into later phases without user approval.
2. Do not approve if plan-defined payload contracts are broken.
3. Do not approve if required validations are missing and no blocker is documented.
4. Do not rewrite the implementation during review unless the user explicitly requests fixes.

## Output Format

Return results in this structure:

1. Gate decision: PASS, CONDITIONAL PASS, or FAIL
2. Findings by severity:
- Critical
- Major
- Minor
3. Evidence list:
- Files checked
- Validations checked
- Contract checks
4. Status update recommendation for the plan table
5. Follow-up actions

## Example Prompts

- /phase-review 检查步骤4完成后是否越界，并判断是否可以进入步骤5
- /phase-review 对第一阶段做验收，核对边界约束和数据格式约定
- /phase-review 审查当前实现是否满足项目修改计划中的契约

## Completion Criteria

This skill is correctly applied when the agent:
- checks the target phase against plan scope and boundaries
- verifies data format and state contracts explicitly
- gives a clear gate decision with evidence
- provides a plan-status update recommendation
