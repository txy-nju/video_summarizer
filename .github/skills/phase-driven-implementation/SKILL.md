---
name: phase-driven-implementation
description: 'Implement a project step by step according to an existing plan. Use when the user asks to 按计划落地、分阶段实现、逐步实现项目、根据项目修改计划写代码、step by step implementation, staged rollout, implement according to roadmap, or wants the agent to follow a project plan strictly instead of improvising.'
argument-hint: 'Describe the plan file and the step or milestone to implement'
user-invocable: true
---

# Step-Driven Implementation

Use this skill when the project already has a written implementation plan and the goal is to turn that plan into code incrementally step by step, with strict step boundaries, explicit contracts, and focused validation.

Primary plan file for this repository:
- [项目修改计划](./references/项目修改计划.md)

## What This Skill Produces

- A narrow implementation slice aligned to the next unfinished step in the plan
- Concrete code changes limited to that step's files and contracts
- Validation for the touched slice immediately after completion
- Short progress reporting tied to plan steps, not ad hoc exploration
- **Agent stops after completing one step; user must explicitly request the next step**

## When to Use

- The user says to implement the project according to the plan
- The user wants step-by-step delivery instead of a large one-shot refactor
- The plan defines steps, boundaries, files, schemas, or constraints
- The implementation must respect architecture layers and data contracts
- The user explicitly requests the next step after one completes

Do not use this skill when:
- The task is a one-off bug fix unrelated to the project roadmap
- There is no written plan or milestone to anchor implementation
- The user only wants brainstorming, not code changes

**Key behavior: Complete one step, validate it, then STOP. Do not continue to the next step without explicit user request.**

## Required Inputs

Before coding, identify:
1. The plan file to follow
2. The current plan status from the plan file
3. The current target step, milestone, or the next unfinished step
4. The concrete files, boundaries, and data contracts for that step

If the user does not name a step, infer the smallest unfinished step from the plan and state that choice explicitly.

## Plan Status Protocol (Mandatory)

Before implementation, the agent must read the current plan status from the plan file and treat it as the single source of execution progress.

Preferred status section in the plan file (tracked at step level):

```markdown
## 计划状态

| Step | Status | Owner | Last Updated | Evidence |
|------|--------|-------|--------------|----------|
| 步骤 1 | not-started | - | - | - |
| 步骤 2 | in-progress | - | 2026-05-13 | tests/api/test_xxx.py passed |
| 步骤 3 | completed | - | 2026-05-13 | pytest tests/tasks/test_yyy.py |
```

Allowed status values:
- `not-started`
- `in-progress`
- `blocked`
- `completed`

If the plan file has no status section:
1. Initialize a minimal `## 计划状态` section before implementation.
2. Mark the chosen step as `in-progress`.
3. Persist this update in the same plan file.

Automatic status updates are required:
- On step start: set target step to `in-progress`.
- On step completion: set target step to `completed` and attach evidence.
- On blocker: set target step to `blocked` and record blocker reason.

Evidence must include at least one of:
- focused test command and pass result
- lint/typecheck pass result for touched slice
- explicit non-executable reason when validation is unavailable

**After completing one step, the agent STOPS and reports completion. The user must explicitly request the next step.**

## Operating Rules

1. **Implement one step and only one step per execution.** Do not continue to the next step.
2. Do not pull work from later steps unless the current step cannot compile or validate without a minimal prerequisite.
3. Keep changes inside the architectural boundaries defined by the plan.
4. Treat the plan as the source of truth for files, contracts, naming, and validation targets.
5. Before any edit, read and lock current plan status from the plan file.
6. After the first substantive edit in a step, run the narrowest available validation before continuing.
7. If validation fails, repair within the same step before widening scope.
8. If the step is too large, split it into the smallest executable slice and finish that slice end to end.
9. After each step, automatically trigger `phase-review` before final status update.
10. Update plan status only after `phase-review` decision is produced, with evidence and timestamp.
11. **After step completion, report results and STOP. Wait for explicit user request to proceed to the next step.**

## Step Execution Workflow

### 1. Read Current Plan Status and Lock Target Step

- Read the `计划状态` section in the plan file first.
- Read the relevant implementation section of the plan.
- Extract four things:
  - Current status snapshot
  - Goal
  - Implementation files
  - Boundary constraints
  - Data format contracts
- Restate the chosen implementation slice in one or two sentences before editing.

Output checklist for this step:
- Status snapshot (step -> status)
- Step name
- Why this step is next
- Files to touch now
- Validation to run after the first edit
- Planned status transition (`not-started -> in-progress`)

### 2. Establish the Local Contract

Before editing, identify the local contract that this step must satisfy:
- API surface
- Schema shape
- persistence model
- workflow state transitions
- message or event payloads

Prefer to encode the contract in one of these forms:
- Pydantic schema
- SQLAlchemy model
- typed function signature
- test expectation
- explicit JSON example in docs or comments when needed

If the plan defines JSON formats or enums, preserve them exactly unless the user explicitly changes the plan.

### 3. Implement the Minimum End-to-End Slice

Apply the smallest code change that makes measurable progress inside the step.

Preferred order:
1. Shared schema or model definitions
2. Repository or persistence logic
3. Service-layer orchestration
4. API or task entrypoint
5. Narrow tests for the slice

Avoid starting with broad route wiring if the real behavior is decided in a lower layer.

### 4. Validate Immediately

After the first substantive edit, run the cheapest falsifying check:
- targeted test
- targeted typecheck or lint
- focused import or startup check
- only use diff review if no executable validation exists

Do not continue patching unrelated parts before this validation.

### 5. Close the Slice

A step slice is complete only when all of the following hold:
- The intended contract is implemented
- The touched files are consistent with the plan
- A focused validation passes or an explicit blocker is documented
- No later-step functionality was pulled in unnecessarily

### 6. Hand Off Cleanly

When reporting completion, summarize in step language:
- Implemented slice
- Files changed
- Validation run
- Remaining work in the same step
- **Next logical step (but DO NOT implement it—wait for user request)**

### 7. Trigger Phase Review Automatically

After finishing the slice and focused validation, invoke the review gate using:
- [phase-review skill](../phase-review/SKILL.md)
- [phase review rubric](../phase-review/references/review-rubric.md)

Review must check:
- scope boundary (no unapproved later-step work)
- architecture boundary constraints
- plan-defined data format contracts
- validation evidence completeness

Accepted review outcomes:
- `PASS`
- `CONDITIONAL PASS`
- `FAIL`

### 8. Update Plan Status Based on Review Outcome

After finishing the slice, update the plan file status table immediately.

Required updates:
- If review outcome is `PASS`, set target step status to `completed`.
- If review outcome is `CONDITIONAL PASS`, set target step status to `completed` and include follow-up actions.
- If review outcome is `FAIL`, set target step status to `blocked` or keep `in-progress` based on blocker severity.
- Update `Last Updated` timestamp.
- Record evidence (review outcome + test command + lint/typecheck command, or blocker reason).

If the step is partially complete:
- Keep status as `in-progress`.
- Append a short progress note in evidence.

### 9. Report Completion and STOP

After step completion:
1. Summarize what was implemented and validated.
2. List files changed.
3. Provide evidence of validation.
4. Identify the next unfinished step in the plan.
5. **STOP AND WAIT FOR USER EXPLICIT REQUEST TO CONTINUE.**

## Branching Logic

### If the step depends on missing prerequisites

- Add only the minimal prerequisite required to unblock the current step.
- Mark it as prerequisite work for the current step, not as opportunistic future work.

### If the plan and code diverge

- Prefer the written plan for new architecture.
- Preserve existing stable behavior unless the step explicitly replaces it.
- If divergence changes public contracts, stop and ask the user which source of truth to follow.

### If a step spans too many files

- Split by executable seam:
  - schema first
  - repository first
  - one endpoint first
  - one worker first
- Finish one seam fully before reporting completion; do not continue to the next seam without user request.

### If tests are missing

- Add the narrowest test that proves the current step contract.
- Do not create broad integration suites unless the current step specifically calls for them.

### If the user asks for a later step directly

- Check whether earlier steps are hard prerequisites.
- If yes, implement only the minimal prerequisite surface and then report completion; wait for user to request the final step.
- If no, implement the requested step and report completion; note that earlier steps were skipped.

## Quality Gates Per Step

Before declaring a step slice done, verify:

- Scope discipline: only current-step files and prerequisites changed
- Layer discipline: route, service, repository, task, and workflow boundaries remain intact
- Contract discipline: request, response, event, and DB payload shapes match the plan
- Validation discipline: one focused executable check ran after edits
- Review discipline: `phase-review` was executed and outcome was recorded
- Handoff discipline: next slice is identified without reopening broad exploration
- Status discipline: plan status is updated in the plan file with timestamp and evidence
- Stopping discipline: implementation STOPS after this step; no automatic continuation to the next step

## Repository-Specific Guidance

For this repository, prefer the plan-defined layering:
- `backend/api`: transport only
- `backend/schemas`: request/response contracts
- `backend/repositories`: persistence access
- `backend/services`: business orchestration
- `backend/tasks`: async execution wrappers and status progression
- `core/workflow`: summary-generation engine, not HTTP-aware

Repository-specific implementation anchors usually come from:
- [项目修改计划](./references/项目修改计划.md)
- `core/workflow/`
- `services/workflow_service.py`

When implementing backend steps here, preserve these rules:
- Do not leak ORM models directly through API responses
- Do not place business decisions in route handlers
- Do not let Celery tasks become the only location of domain logic
- Do not bypass plan-defined JSON formats for `Config`, `Attachments`, `Cited_Sources`, vector ID arrays, or WebSocket payloads

**Step-specific rule:** After one step completes and is validated, implementation stops. The user must explicitly request the next step.

## Example Prompts

- `/phase-driven-implementation 按项目修改计划先实现步骤1，搭好 FastAPI 骨架`
- `/phase-driven-implementation 根据 docs/analysis/项目修改计划 实现步骤3 的数据库模型和 Alembic 迁移`
- `/phase-driven-implementation 严格按计划推进，只做步骤4里的知识库 API 和对应 schema`
- `/phase-driven-implementation 实现当前计划里下一步最小可执行切片，并完成验证`
- `/phase-driven-implementation 继续实现下一步` (user explicitly requests next step after previous completion)

## Completion Criteria

This skill has been applied correctly when the agent:
- chooses a specific step slice
- implements only that step and its strict prerequisites
- validates immediately after the first substantive edit
- triggers phase-review automatically after each finished step slice
- reports progress against the plan steps rather than generic coding progress
- identifies the next slice without silently expanding scope
- reads plan status before execution and updates plan status automatically after execution based on review outcome
- **STOPS after completing one step and explicitly waits for the user to request the next step**