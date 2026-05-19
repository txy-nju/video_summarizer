# 前后端交互接口文档（代码对齐重写版）

## 1. 文档元信息
- 项目：video_summarizer
- 版本：v2026-05-18
- 范围：FastAPI 已注册全部 controller 路由 + WebSocket + SSE + FCM 交互载荷
- 生成依据：
  - 路由注册：backend/app_factory.py
  - 路由定义：backend/api/routes/*.py, backend/websocket/handlers.py
  - 协议模型：backend/schemas/*.py, backend/schemas/common.py, backend/auth/models.py, backend/websocket/schemas.py
  - 语义补充：.github/skills/phase-driven-implementation/references/项目修改计划.md
- 判定优先级：路由行为 > schema > 计划语义

## 2. 全局协议约定

### 2.1 Base URL 与鉴权
- Base URL：/
- API 前缀：/api/v1
- 鉴权：Authorization: Bearer <access_token>
- 免鉴权：
  - GET /health
  - POST /api/v1/auth/register
  - POST /api/v1/auth/login
  - POST /api/v1/auth/refresh

### 2.2 通用 Header
- x-request-id：可选，服务端会透传到响应头
- x-trace-id：可选，服务端会透传到响应头

### 2.3 命名、时间、ID
- 字段命名：snake_case
- 时间格式：UTC ISO 8601（例如 2026-05-18T10:00:00Z）
- 业务 ID：字符串（协议层未强制 UUID）

### 2.4 通用成功信封
```json
{
  "status": "success",
  "data": {},
  "pagination": null,
  "meta": {
    "request_id": "req_001",
    "timestamp": "2026-05-18T10:00:00Z"
  }
}
```

### 2.5 通用错误信封（Schema）
```json
{
  "status": "error",
  "data": null,
  "error": {
    "code": "UNKNOWN_ERROR",
    "message": "...",
    "details": {},
    "is_retryable": false,
    "retry_after": null
  },
  "meta": {
    "request_id": "req_001",
    "timestamp": "2026-05-18T10:00:00Z"
  }
}
```

当前实现注意：多数 HTTP 异常使用 FastAPI 默认结构 {"detail":"..."}。

### 2.6 非统一信封例外
- auth：仅 status + data
- file_upload：TUS Header + DTO/裸 dict
- devices：DTO/裸 dict
- WebSocket/SSE：流式协议，不走 JSON 信封

## 3. 实时通信协议

### 3.1 WebSocket：/ws/progress
- 连接参数：
  - token（必填，JWT access token）
  - last_sequence（可选，重连确认）
- 心跳：客户端 ping，服务端 pong
- 鉴权失败：close code=4001

WebSocket 事件统一信封（WSEventEnvelope）：
```json
{
  "event_id": "evt_xxx",
  "schema_version": "1.0",
  "event_type": "progress",
  "trace_id": "trc_xxx",
  "produced_at": "2026-05-18T10:00:00+00:00",
  "tenant_id": "default",
  "user_id": "usr_001",
  "scope": "video_summary_task",
  "scope_id": "task_001",
  "sequence": 42,
  "stage": "rag_retrieval",
  "substage": "chunk_processing",
  "status": "RUNNING",
  "progress": 45,
  "message": "Chunk processing: 9/20",
  "payload": {},
  "source": {
    "service": "progress_publish_service",
    "instance_id": "worker-01"
  }
}
```

枚举：
- event_type: progress/completed/error/status_update/reconnect_ack
- scope: video_resource/video_summary_task/video_qa/global_chat
- stage: extraction/transcribing/extracting_keyframes/rag_retrieval/llm_reasoning/synthesis/cleanup

重连确认示例：
```json
{
  "event_type": "reconnect_ack",
  "sequence": 88,
  "status": "RECONNECTED",
  "payload": {"last_sequence": 88}
}
```

### 3.2 SSE：QA 生成流
- 端点 1：POST /api/v1/tasks/{task_id}/time-travel-qa/stream
- 端点 2：POST /api/v1/kbs/{kbid}/chats/{chat_id}/qa/stream
- Header：
  - Content-Type: text/event-stream
  - Cache-Control: no-cache
  - Connection: keep-alive
- 事件类型：start/delta/done/error

示例（time-travel QA）：
```text
event: start
data: {"task_id":"task_001","qa_id":"qa_001","timestamp":"2026-05-18T10:00:00Z"}

event: delta
data: {"task_id":"task_001","qa_id":"qa_001","chunk":"这是","sequence":1,"timestamp":"2026-05-18T10:00:01Z"}

event: done
data: {"task_id":"task_001","qa_id":"qa_001","answer_content":"这是最终答案","timestamp":"2026-05-18T10:00:02Z"}
```

示例（global QA）：
```text
event: start
data: {"kbid":"kb_001","chat_id":"chat_001","qa_id":"gqa_001","timestamp":"2026-05-18T10:00:00Z"}

event: delta
data: {"kbid":"kb_001","chat_id":"chat_001","qa_id":"gqa_001","chunk":"这是","sequence":1,"timestamp":"2026-05-18T10:00:01Z"}

event: done
data: {"kbid":"kb_001","chat_id":"chat_001","qa_id":"gqa_001","answer_content":"这是最终答案","cited_sources":[],"timestamp":"2026-05-18T10:00:02Z"}
```

### 3.3 FCM 推送载荷
前端接收载荷结构：
```json
{
  "title": "✅ 视频总结已完成",
  "body": "您的视频总结 [abcd1234] 已生成，点击查看",
  "data": {
    "scope": "video_summary_task",
    "scope_id": "task_001",
    "deep_link": "app://tasks/task_001"
  }
}
```

触发语义（后端已实现）：
- notify_workflow_approval_required
- notify_workflow_completed
- notify_workflow_failed

## 4. 接口总览（全覆盖）

### 4.1 System
| Method | Path | Auth | Status |
|---|---|---|---|
| GET | /health | 否 | 200 |

### 4.2 Auth
| Method | Path | Auth | Status |
|---|---|---|---|
| POST | /api/v1/auth/register | 否 | 200 |
| POST | /api/v1/auth/login | 否 | 200 |
| POST | /api/v1/auth/refresh | 否 | 200 |
| GET | /api/v1/auth/me | 是 | 200 |

### 4.3 Knowledge Base
| Method | Path | Status |
|---|---|---|
| POST | /api/v1/kbs | 201 |
| GET | /api/v1/kbs | 200 |
| GET | /api/v1/kbs/{kbid} | 200 |
| PATCH | /api/v1/kbs/{kbid} | 200 |
| DELETE | /api/v1/kbs/{kbid} | 200 |
| POST | /api/v1/kbs/{kbid}/videos | 200 |
| GET | /api/v1/kbs/{kbid}/videos | 200 |
| DELETE | /api/v1/kbs/{kbid}/videos/{video_id} | 200 |

### 4.4 Video Resource
| Method | Path | Status |
|---|---|---|
| POST | /api/v1/videos | 201 |
| GET | /api/v1/videos | 200 |
| GET | /api/v1/videos/{video_id} | 200 |
| PATCH | /api/v1/videos/{video_id} | 200 |
| DELETE | /api/v1/videos/{video_id} | 202 |

### 4.5 Video Summary Task
| Method | Path | Status |
|---|---|---|
| POST | /api/v1/tasks | 201 |
| GET | /api/v1/tasks | 200 |
| GET | /api/v1/tasks/{task_id} | 200 |
| PATCH | /api/v1/tasks/{task_id} | 200 |
| DELETE | /api/v1/tasks/{task_id} | 200 |
| POST | /api/v1/tasks/{task_id}/start-analysis | 202 |
| POST | /api/v1/tasks/{task_id}/approve-and-finalize | 202 |
| POST | /api/v1/tasks/{task_id}/time-travel-qa/stream | 200 (SSE) |

### 4.6 Video QA
| Method | Path | Status |
|---|---|---|
| POST | /api/v1/tasks/{task_id}/qa | 201 |
| GET | /api/v1/tasks/{task_id}/qa | 200 |
| GET | /api/v1/tasks/{task_id}/qa/{qa_id} | 200 |
| PATCH | /api/v1/tasks/{task_id}/qa/{qa_id} | 200 |
| DELETE | /api/v1/tasks/{task_id}/qa/{qa_id} | 200 |

### 4.7 Global Chat
| Method | Path | Status |
|---|---|---|
| POST | /api/v1/kbs/{kbid}/chats | 201 |
| GET | /api/v1/kbs/{kbid}/chats | 200 |
| GET | /api/v1/kbs/{kbid}/chats/{chat_id} | 200 |
| PATCH | /api/v1/kbs/{kbid}/chats/{chat_id} | 200 |
| DELETE | /api/v1/kbs/{kbid}/chats/{chat_id} | 200 |

### 4.8 Global QA
| Method | Path | Status |
|---|---|---|
| POST | /api/v1/kbs/{kbid}/chats/{chat_id}/qa/stream | 200 (SSE) |
| POST | /api/v1/kbs/{kbid}/chats/{chat_id}/qa | 201 |
| GET | /api/v1/kbs/{kbid}/chats/{chat_id}/qa | 200 |
| GET | /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id} | 200 |
| PATCH | /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id} | 200 |
| DELETE | /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id} | 200 |

### 4.9 Upload (TUS)
| Method | Path | Status |
|---|---|---|
| POST | /api/v1/uploads | 201 |
| HEAD | /api/v1/uploads/{upload_id} | 204 |
| PATCH | /api/v1/uploads/{upload_id} | 200/204 |
| DELETE | /api/v1/uploads/{upload_id} | 200 |
| GET | /api/v1/uploads/{upload_id} | 200 |

### 4.10 Devices
| Method | Path | Status |
|---|---|---|
| POST | /api/v1/devices | 200 |
| DELETE | /api/v1/devices/{device_token_id} | 200 |
| GET | /api/v1/devices | 200 |

### 4.11 WebSocket
| 协议 | Path | Auth |
|---|---|---|
| WS | /ws/progress | query token |

## 5. 规范化模型（关键字段）

### 5.1 Knowledge Base
- KnowledgeBaseCreateRequest:
  - name: string, min=1, max=255
  - category: string?, max=128
  - description: string?, max=2000
  - config: retrieval/top_k(>=1), rerank(bool), allow_web_search(bool), temperature(0~2)

### 5.2 Video Resource
- VideoResourceCreateRequest:
  - file_name: string, min=1, max=255
- VideoResourceView 关键状态：
  - transcribe_status
  - frame_extraction_status
  - extract_completed_at
- 语义：extract_completed_at 非空 + 双状态 COMPLETED 才允许创建总结任务（代码与计划一致）

### 5.3 Video Summary Task
- VideoSummaryTaskCreateRequest:
  - kbid/video_id: min=1,max=64
  - user_initial_preference: max=5000
- workflow_state:
  - DRAFT_GENERATING
  - WAITING_USER_APPROVAL
  - FINAL_GENERATING
  - COMPLETED
  - FAILED
- 用户 PATCH 仅开放：draft_summary/user_guidance/title

### 5.4 Time Travel QA
- TimeTravelQARequest:
  - timestamp: ^\d{2}:\d{2}:\d{2}$
  - question: min=1,max=5000
  - window_seconds: 5~300, default=20

### 5.5 Upload
- InitUploadRequest:
  - file_name: 1~512
  - total_size: >0 且 <=10GB
- 固定分片：10 MiB

### 5.6 Device/FCM
- DeviceRegisterRequest:
  - device_token: 1~512
  - platform: android/ios/web
  - app_version: <=32
  - device_id: 1~128
- FCM data:
  - scope
  - scope_id
  - deep_link

## 6. 端点详细示例（每个端点至少一例）

### 6.1 System
GET /health
```json
{"status":"ok"}
```

### 6.2 Auth
POST /api/v1/auth/register
```json
{"username":"alice","password":"Secret123!"}
```
```json
{"status":"success","data":{"user_id":"usr_001","username":"alice"}}
```

POST /api/v1/auth/login
```json
{"username":"alice","password":"Secret123!","device_id":"android_001"}
```
```json
{"status":"success","data":{"access_token":"jwt_access","refresh_token":"jwt_refresh","token_type":"bearer","expires_in":1800,"user":{"user_id":"usr_001","username":"alice"}}}
```

POST /api/v1/auth/refresh
```json
{"refresh_token":"jwt_refresh","device_id":"android_001"}
```
```json
{"status":"success","data":{"access_token":"new_access","refresh_token":"new_refresh","token_type":"bearer","expires_in":1800,"user":{"user_id":"usr_001","username":"alice"}}}
```

GET /api/v1/auth/me
```json
{"status":"success","data":{"user_id":"usr_001","username":"alice"}}
```

### 6.3 KB & Video 绑定
POST /api/v1/kbs
```json
{"name":"LLM库","category":"tech","description":"说明","config":{"retrieval":{"top_k":5,"rerank":true},"tool_preferences":{"allow_web_search":false},"llm_policy":{"temperature":0.2}}}
```
```json
{"status":"success","data":{"kbid":"kb_001","owner_id":"usr_001","name":"LLM库","category":"tech","description":"说明","vector_collection_name":"kb_usr_001","config":{"retrieval":{"top_k":5,"rerank":true},"tool_preferences":{"allow_web_search":false},"llm_policy":{"temperature":0.2}},"created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/kbs?page=1&page_size=20
```json
{"status":"success","data":[],"pagination":{"page":1,"page_size":20,"total":0,"has_next":false,"next_cursor":null},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/kbs/{kbid}
```json
{"status":"success","data":{"kbid":"kb_001","owner_id":"usr_001","name":"LLM库","category":"tech","description":"说明","vector_collection_name":"kb_usr_001","config":{"retrieval":{"top_k":5,"rerank":true},"tool_preferences":{"allow_web_search":false},"llm_policy":{"temperature":0.2}},"created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

PATCH /api/v1/kbs/{kbid}
```json
{"name":"LLM库V2"}
```
```json
{"status":"success","data":{"kbid":"kb_001","owner_id":"usr_001","name":"LLM库V2","category":"tech","description":"说明","vector_collection_name":"kb_usr_001","config":{"retrieval":{"top_k":5,"rerank":true},"tool_preferences":{"allow_web_search":false},"llm_policy":{"temperature":0.2}},"created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

DELETE /api/v1/kbs/{kbid}
```json
{"status":"success","data":{"kbid":"kb_001"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

POST /api/v1/kbs/{kbid}/videos
```json
{"video_id":"vid_001"}
```
```json
{"status":"success","data":{"kbid":"kb_001","video_id":"vid_001"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/kbs/{kbid}/videos
```json
{"status":"success","data":[{"video_id":"vid_001","file_name":"demo.mp4","created_at":"2026-05-18T10:00:00Z"}],"pagination":{"page":1,"page_size":20,"total":1,"has_next":false,"next_cursor":null},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

DELETE /api/v1/kbs/{kbid}/videos/{video_id}
```json
{"status":"success","data":{"kbid":"kb_001","video_id":"vid_001"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

### 6.4 Video Resource
POST /api/v1/videos
```json
{"file_name":"demo.mp4"}
```
```json
{"status":"success","data":{"video_id":"vid_001","owner_id":"usr_001","file_name":"demo.mp4","oss_key":"","duration":0,"full_transcript":null,"transcribe_status":"UPLOADED","transcript_vector_ids":null,"keyframes":null,"frame_extraction_status":"UPLOADED","keyframes_oss_prefix":null,"extract_completed_at":null,"created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/videos
```json
{"status":"success","data":[],"pagination":{"page":1,"page_size":20,"total":0,"has_next":false,"next_cursor":null},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/videos/{video_id}
```json
{"status":"success","data":{"video_id":"vid_001","owner_id":"usr_001","file_name":"demo.mp4","oss_key":"","duration":0,"full_transcript":null,"transcribe_status":"UPLOADED","transcript_vector_ids":null,"keyframes":null,"frame_extraction_status":"UPLOADED","keyframes_oss_prefix":null,"extract_completed_at":null,"created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

PATCH /api/v1/videos/{video_id}
```json
{"file_name":"demo_v2.mp4"}
```
```json
{"status":"success","data":{"video_id":"vid_001","owner_id":"usr_001","file_name":"demo_v2.mp4","oss_key":"","duration":0,"full_transcript":null,"transcribe_status":"UPLOADED","transcript_vector_ids":null,"keyframes":null,"frame_extraction_status":"UPLOADED","keyframes_oss_prefix":null,"extract_completed_at":null,"created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

DELETE /api/v1/videos/{video_id}
```json
{"status":"success","data":{"video_id":"vid_001"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

### 6.5 Summary Task + Workflow
POST /api/v1/tasks
```json
{"kbid":"kb_001","video_id":"vid_001","user_initial_preference":"关注技术细节"}
```
```json
{"status":"success","data":{"task_id":"task_001","kbid":"kb_001","video_id":"vid_001","workflow_state":"DRAFT_GENERATING","user_initial_preference":"关注技术细节","draft_summary":null,"user_guidance":null,"final_summary":null,"title":null,"summary_vector_ids":null,"created_at":"2026-05-18T10:00:00Z","updated_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/tasks
```json
{"status":"success","data":[],"pagination":{"page":1,"page_size":20,"total":0,"has_next":false,"next_cursor":null},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/tasks/{task_id}
```json
{"status":"success","data":{"task_id":"task_001","kbid":"kb_001","video_id":"vid_001","workflow_state":"WAITING_USER_APPROVAL","user_initial_preference":"关注技术细节","draft_summary":"...","user_guidance":null,"final_summary":null,"title":"总结-task_001","summary_vector_ids":null,"created_at":"2026-05-18T10:00:00Z","updated_at":"2026-05-18T10:05:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:05:00Z"}}
```

PATCH /api/v1/tasks/{task_id}
```json
{"draft_summary":"人工修订草稿","user_guidance":"输出更简洁","title":"演讲总结"}
```
```json
{"status":"success","data":{"task_id":"task_001","kbid":"kb_001","video_id":"vid_001","workflow_state":"WAITING_USER_APPROVAL","user_initial_preference":"关注技术细节","draft_summary":"人工修订草稿","user_guidance":"输出更简洁","final_summary":null,"title":"演讲总结","summary_vector_ids":null,"created_at":"2026-05-18T10:00:00Z","updated_at":"2026-05-18T10:06:00Z"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:06:00Z"}}
```

DELETE /api/v1/tasks/{task_id}
```json
{"status":"success","data":{"task_id":"task_001"},"meta":{"request_id":"req_001","timestamp":"2026-05-18T10:10:00Z"}}
```

POST /api/v1/tasks/{task_id}/start-analysis
```json
{}
```
```json
{"status":"success","data":{"task_id":"task_001","celery_task_id":"celery_001","thread_id":"task_001","workflow_state":"DRAFT_GENERATING","accepted_at":"2026-05-18T10:10:00Z","message":"Phase-1 analysis workflow dispatched"},"meta":{"request_id":"req_002","timestamp":"2026-05-18T10:10:00Z"}}
```

POST /api/v1/tasks/{task_id}/approve-and-finalize
```json
{"edited_aggregated_chunk_insights":"请保留重点","human_guidance":"输出更简洁"}
```
```json
{"status":"success","data":{"task_id":"task_001","celery_task_id":"celery_002","thread_id":"task_001","workflow_state":"FINAL_GENERATING","accepted_at":"2026-05-18T10:12:00Z","message":"Phase-2 finalization workflow dispatched"},"meta":{"request_id":"req_003","timestamp":"2026-05-18T10:12:00Z"}}
```

POST /api/v1/tasks/{task_id}/time-travel-qa/stream
```json
{"timestamp":"00:10:00","question":"这一段讲什么？","window_seconds":20}
```
返回：SSE 流（见第 3.2 节）。

### 6.6 Video QA
POST /api/v1/tasks/{task_id}/qa
```json
{"task_id":"task_001","start_time":"00:10:00","end_time":"00:11:00","question_content":"这里在讲什么？","attachments":[]}
```
```json
{"status":"success","data":{"qa_id":"qa_001","task_id":"task_001","start_time":"00:10:00","end_time":"00:11:00","question_content":"这里在讲什么？","answer_content":null,"attachments":[],"question_time":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_005","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/tasks/{task_id}/qa
```json
{"status":"success","data":[],"pagination":{"page":1,"page_size":20,"total":0,"has_next":false,"next_cursor":null},"meta":{"request_id":"req_005","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/tasks/{task_id}/qa/{qa_id}
```json
{"status":"success","data":{"qa_id":"qa_001","task_id":"task_001","start_time":"00:10:00","end_time":"00:11:00","question_content":"这里在讲什么？","answer_content":"...","attachments":[],"question_time":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_005","timestamp":"2026-05-18T10:00:00Z"}}
```

PATCH /api/v1/tasks/{task_id}/qa/{qa_id}
```json
{"regenerate":true}
```
```json
{"status":"success","data":{"qa_id":"qa_001","task_id":"task_001","start_time":"00:10:00","end_time":"00:11:00","question_content":"这里在讲什么？","answer_content":"...","attachments":[],"question_time":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_005","timestamp":"2026-05-18T10:00:00Z"}}
```

DELETE /api/v1/tasks/{task_id}/qa/{qa_id}
```json
{"status":"success","data":{"qa_id":"qa_001"},"meta":{"request_id":"req_005","timestamp":"2026-05-18T10:00:00Z"}}
```

### 6.7 Global Chat + Global QA
POST /api/v1/kbs/{kbid}/chats
```json
{"kbid":"kb_001","chat_title":"新会话"}
```
```json
{"status":"success","data":{"chat_id":"chat_001","kbid":"kb_001","chat_title":"新会话","created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_006","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/kbs/{kbid}/chats
```json
{"status":"success","data":[],"pagination":{"page":1,"page_size":20,"total":0,"has_next":false,"next_cursor":null},"meta":{"request_id":"req_006","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/kbs/{kbid}/chats/{chat_id}
```json
{"status":"success","data":{"chat_id":"chat_001","kbid":"kb_001","chat_title":"新会话","created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_006","timestamp":"2026-05-18T10:00:00Z"}}
```

PATCH /api/v1/kbs/{kbid}/chats/{chat_id}
```json
{"chat_title":"更新标题"}
```
```json
{"status":"success","data":{"chat_id":"chat_001","kbid":"kb_001","chat_title":"更新标题","created_at":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_006","timestamp":"2026-05-18T10:01:00Z"}}
```

DELETE /api/v1/kbs/{kbid}/chats/{chat_id}
```json
{"status":"success","data":{"chat_id":"chat_001"},"meta":{"request_id":"req_006","timestamp":"2026-05-18T10:02:00Z"}}
```

POST /api/v1/kbs/{kbid}/chats/{chat_id}/qa/stream
```json
{"question_content":"跨视频问题","attachments":[]}
```
返回：SSE 流（start/delta/done/error，见第 3.2 节 global QA 示例）。

POST /api/v1/kbs/{kbid}/chats/{chat_id}/qa
```json
{"question_content":"跨视频问题","attachments":[]}
```
```json
{"status":"success","data":{"qa_id":"gqa_001","chat_id":"chat_001","question_content":"跨视频问题","answer_content":null,"attachments":[],"cited_sources":[],"question_time":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_007","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/kbs/{kbid}/chats/{chat_id}/qa
```json
{"status":"success","data":[],"pagination":{"page":1,"page_size":20,"total":0,"has_next":false,"next_cursor":null},"meta":{"request_id":"req_007","timestamp":"2026-05-18T10:00:00Z"}}
```

GET /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}
```json
{"status":"success","data":{"qa_id":"gqa_001","chat_id":"chat_001","question_content":"跨视频问题","answer_content":"...","attachments":[],"cited_sources":[],"question_time":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_007","timestamp":"2026-05-18T10:00:00Z"}}
```

PATCH /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}
```json
{"regenerate":true}
```
```json
{"status":"success","data":{"qa_id":"gqa_001","chat_id":"chat_001","question_content":"跨视频问题","answer_content":"...","attachments":[],"cited_sources":[],"question_time":"2026-05-18T10:00:00Z"},"meta":{"request_id":"req_007","timestamp":"2026-05-18T10:00:00Z"}}
```

DELETE /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}
```json
{"status":"success","data":{"qa_id":"gqa_001"},"meta":{"request_id":"req_007","timestamp":"2026-05-18T10:00:00Z"}}
```

### 6.8 Upload (TUS)
POST /api/v1/uploads
```json
{"file_name":"demo.mp4","total_size":524288000}
```
```json
{"upload_id":"upl_001","chunk_size":10485760,"expires_at":"2026-05-18T12:00:00Z"}
```

HEAD /api/v1/uploads/{upload_id}
```json
{"Tus-Resumable":"1.0.0","Upload-Offset":"10485760","Upload-Length":"524288000"}
```

PATCH /api/v1/uploads/{upload_id}
```json
{"Upload-Offset":10485760,"Tus-Resumable":"1.0.0","body":"<binary chunk>"}
```
响应：
- 204（未完成）
- 200（全部分片完成）

DELETE /api/v1/uploads/{upload_id}
```json
{"upload_id":"upl_001","status":"cancelled"}
```

GET /api/v1/uploads/{upload_id}
```json
{"upload_id":"upl_001","uploaded_size":31457280,"total_size":524288000,"uploaded_chunks":[0,1,2]}
```

### 6.9 Devices
POST /api/v1/devices
```json
{"device_token":"fcm_token_xxx","platform":"android","app_version":"1.0.0","device_id":"android_001"}
```
```json
{"device_token_id":"devtok_001","platform":"android","device_id":"android_001","registered_at":"2026-05-18T10:00:00Z"}
```

DELETE /api/v1/devices/{device_token_id}
```json
{"status":"success","message":"Device unregistered"}
```

GET /api/v1/devices
```json
{"status":"success","data":[{"device_token_id":"devtok_001","platform":"android","device_id":"android_001","app_version":"1.0.0","registered_at":"2026-05-18T10:00:00Z"}]}
```

## 7. 错误映射
| 场景 | HTTP/协议 | 典型响应 |
|---|---|---|
| 缺 token | 401 | {"detail":"Missing token"} |
| token 非法 | 401 | {"detail":"..."} |
| 资源不存在 | 404 | {"detail":"... not found"} |
| path/body id 不一致 | 400 | {"detail":"... must match"} |
| 校验失败 | 422 | FastAPI validation error |
| workflow 状态非法 | 422 | {"detail":"Task must be ..."} |
| TUS 版本不匹配 | 412 | {"detail":"Unsupported Tus-Resumable version: ..."} |
| WebSocket 无 token | close 4001 | reason=missing_token |
| WebSocket token 非法 | close 4001 | reason=invalid_token/invalid_token_type |

## 8. 一致性检查与冲突报告

### 8.1 对齐结论
- 已覆盖 create_app() 注册的全部 HTTP 路由与 /ws/progress。
- 已覆盖 SSE 端点：
  - /api/v1/tasks/{task_id}/time-travel-qa/stream
  - /api/v1/kbs/{kbid}/chats/{chat_id}/qa/stream
- 已覆盖设备注册与 FCM 推送载荷契约。

### 8.2 冲突/差异
1. ApproveAndFinalizeResponse 的 schema 描述像“已完成”，但实际路由返回 202 + FINAL_GENERATING（异步受理）。
2. 计划文档提到重连历史补发窗口；当前代码仅 reconnect_ack，不做历史事件回放。
3. 计划中有 Redis Streams 统一事件源叙述；当前实时进度分发实现是 Redis Pub/Sub。
4. devices/file_upload 未采用统一 meta/pagination 信封，需前端按端点区分解析。
5. global_chat.py 与 global_qa.py 同时存在 Global QA 模型定义，路由实际使用 global_qa.py。

### 8.3 推断项（inferred）
- FCM/WS 的 scope_id 为前端目标实体主键，用于 deep_link 导航。
- summary_vector_ids 为系统内部字段，前端不应直接写入。

## 9. 覆盖检查
- [x] 路由注册全覆盖
- [x] 每个端点至少一组请求/响应示例
- [x] WebSocket 协议字段与枚举完整
- [x] SSE 事件协议完整
- [x] FCM 设备注册与载荷契约完整
- [x] 错误映射与冲突说明已列出
