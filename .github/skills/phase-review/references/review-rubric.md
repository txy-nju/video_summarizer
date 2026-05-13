# Phase Review Rubric

Use this rubric as the canonical checklist for phase acceptance.

## A. Scope Boundary

- Are all changed files inside the target phase scope?
- Are out-of-phase files strictly prerequisite and minimal?
- Are any mandatory phase files missing?

Fail conditions:
- Unapproved implementation of later-phase features
- Missing mandatory deliverable in the target phase

## B. Architecture Boundary

- Route layer handles protocol concerns only
- Service layer contains orchestration and business rules
- Repository layer contains persistence access only
- Task layer wraps async execution and status progression
- Workflow layer is not HTTP-coupled

Fail conditions:
- Business decisions implemented in route handlers
- Persistence logic implemented in routes or tasks directly
- Tight coupling between workflow and transport layer

## C. Data Contract Compliance

- Request schemas match plan contracts
- Response schemas match plan contracts
- Error schema includes required fields
- Enum sets and state transitions are legal
- JSON fields match agreed structures:
  - Config
  - Attachments
  - Cited_Sources
  - Transcript_Vector_IDs
  - Summary_Vector_IDs
  - websocket payloads

Fail conditions:
- Contract mismatch on required fields
- Invalid enum/state transition
- Broken cited source or websocket payload format

## D. Validation Evidence

- At least one focused executable validation exists
- Validation aligns with touched scope
- Failing checks are either fixed or explicitly tracked as blocker

Fail conditions:
- No focused validation and no blocker reason
- Evidence unrelated to changed scope

## E. Gate Decision Matrix

- PASS:
  - No critical findings
  - No major contract violation
  - Validation evidence present

- CONDITIONAL PASS:
  - No critical findings
  - Minor findings only
  - Follow-up items explicitly listed

- FAIL:
  - Any critical finding
  - Any major contract violation
  - Missing validation evidence

## F. Plan Status Recommendation

- PASS -> completed
- CONDITIONAL PASS -> completed with follow-up note
- FAIL -> blocked or in-progress with blocker reason

## G. Recommended Evidence Fields

When writing review output or updating plan status, include:
- Phase name
- Review date (UTC)
- Decision
- Files reviewed
- Validation command and result summary
- Top findings
- Next action
