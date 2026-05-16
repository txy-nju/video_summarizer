# API Interface Document Template

## 1. Document Metadata
- Project:
- Version:
- Generated At (UTC):
- Source Commit (optional):
- Scope:

## 2. Global Protocol Conventions
- Base URL:
- Auth method (JWT/Bearer):
- Common headers:
- Time format:
- ID format:
- Naming convention:

### 2.1 Success Envelope
```json
{
  "status": "success",
  "data": {},
  "pagination": null,
  "meta": {
    "request_id": "...",
    "timestamp": "..."
  }
}
```

### 2.2 Error Envelope
```json
{
  "status": "error",
  "data": null,
  "error": {
    "code": "...",
    "message": "...",
    "details": {},
    "is_retryable": false,
    "retry_after": null
  },
  "meta": {
    "request_id": "...",
    "timestamp": "..."
  }
}
```

## 3. Endpoint Inventory
| Domain | Method | Path | Auth | Request Model | Response Model | Status Code |
|---|---|---|---|---|---|---|

## 4. Endpoint Details

### 4.x [METHOD] /path
- Purpose:
- Auth:
- Path params:
- Query params:
- Request body:
- Success response:
- Error responses:
- Field semantics:
- Notes:

#### Request Example
```json
{}
```

#### Success Example
```json
{}
```

#### Error Example
```json
{}
```

## 5. Error and Status Mapping
| Endpoint | HTTP Status | Error Code | Meaning | Retryable |
|---|---|---|---|---|

## 6. Contract Mismatch and Assumptions
- Mismatch items:
- Inferred semantics:
- Needs confirmation:

## 7. Coverage Checklist
- [ ] All routers in app registration covered
- [ ] All route methods/paths covered
- [ ] All request/response models mapped
- [ ] Examples are schema-valid
- [ ] Field semantics have source tags (code/plan/inferred)
