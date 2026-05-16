---
name: backend-api-interface-spec
description: 'Generate a strict frontend-backend API interaction specification from backend controllers/routes. Use when user asks 接口文档, API 契约, 前后端交互定义, controller 对齐文档, route/schema mapping, 字段语义说明, or wants ambiguity-free request/response definitions aligned with code and 项目修改计划.'
argument-hint: '文档目标读者、输出语言、是否需要示例 JSON 与错误码映射'
---

# Backend API Interface Specification

## Skill Goal
Produce a detailed, unambiguous API interaction document for frontend-backend integration by reading controller-layer source code and contract schemas.

The document must stay strictly aligned with current project routes, payloads, and response envelopes.

## Source of Truth (Read Order)
1. Route registration and enabled controllers:
   - `backend/app_factory.py`
   - `backend/api/routes/*.py`
2. Request/response contracts:
   - `backend/schemas/*.py`
   - `backend/schemas/common.py`
3. Service boundary behavior (for semantics and state expectations):
   - `backend/services/*.py`
4. Domain semantics and field meaning reference:
   - `.github/skills/phase-driven-implementation/references/项目修改计划.md`

If there is any discrepancy, use this priority:
- Runtime route behavior from controller code first
- Schema definitions second
- Plan text for semantic explanation third

## What This Skill Produces
1. Interface definition document that includes:
   - Global protocol conventions
   - Authentication and headers
   - Endpoint inventory
   - Per-endpoint request and response contracts
   - Field-by-field semantic explanations
   - Error response mapping
   - Pagination/filter contract
2. Consistency report (inline or appendix):
   - route-schema-plan alignment status
   - assumptions and unresolved ambiguities

## Procedure

### 1. Lock Endpoint Inventory
- Enumerate all routers included in `create_app()` from `backend/app_factory.py`.
- For each route function in `backend/api/routes/*.py`, collect:
  - HTTP method
  - full path
  - route tags
  - status code
  - request models (path/query/body)
  - response model class

### 2. Extract Contract Shapes
- Resolve each referenced schema in `backend/schemas/*.py`.
- Capture exact field contract:
  - field name
  - type
  - required/optional
  - default value
  - constraints (`ge`, `le`, regex, enum)
- Capture global envelope from `backend/schemas/common.py`:
  - success shape
  - error shape
  - pagination shape
  - meta shape

### 3. Annotate Field Semantics
- Explain each field meaning in business terms.
- Prefer plan-aligned semantics from `项目修改计划.md`.
- When plan has no explicit meaning, infer from schema name + service usage, and mark as "inferred".

### 4. Build Document Using Canonical Structure
Use template: [API Interface Document Template](./references/api-interface-template.md)

Minimum sections:
1. Scope and version
2. Global conventions (time, ID, casing, envelope)
3. Auth and common headers
4. Endpoint summary table
5. Detailed endpoint contracts (one subsection per route)
6. Error code/HTTP status mapping
7. Change log and assumptions

### 5. Consistency Checks (Mandatory)
Before finalizing, verify:
- Every documented route exists in controller code
- Every documented field exists in schema code
- No undocumented required field remains
- Status code and response envelope match route decorator and model
- Pagination/filter docs match actual query params
- No route path drift from code

### 6. Completion Gate
This skill is complete only if:
- Document has zero placeholder fields
- All controller routes included in app registration are covered
- Ambiguities are explicitly listed with resolution notes
- Output is readable by frontend engineers without opening backend source

## Branching Logic

### If route code and schema conflict
- Trust route decorator and function signature for protocol behavior.
- Record mismatch in "Contract Mismatch" section.
- Do not silently normalize conflicting definitions.

### If multiple schemas can map to one endpoint
- Use actual request/response model references from that endpoint.
- Mention alternate models only if code branches by runtime condition.

### If semantics are missing in code comments
- Pull meaning from `项目修改计划.md`.
- If still unclear, mark explicitly as "inferred from usage".

### If user asks for strict no-assumption output
- Keep only code-proven facts.
- Move all inferred meaning to a separate "Needs Confirmation" block.

## Output Quality Rules
1. Do not invent endpoints, fields, or enums.
2. Keep naming exactly as code (snake_case, path prefix, tag).
3. Use deterministic wording; avoid vague terms like "usually" or "maybe".
4. For each endpoint, include at least one valid request and response JSON example.
5. Ensure error payload includes `code`, `message`, `is_retryable`, `retry_after` when defined.

## Example Prompts
- `/backend-api-interface-spec 生成中文前后端接口交互定义文档，覆盖全部 controller，附 JSON 示例`
- `/backend-api-interface-spec 仅输出认证和知识库相关接口，严格按当前代码，不要任何推测`
- `/backend-api-interface-spec 产出给前端的联调文档，要求包含错误码映射和字段含义`
