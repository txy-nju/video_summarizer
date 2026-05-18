# 前后端接口交互定义文档（Controller 严格对齐版）

## 1. 文档范围与版本
- 项目：video_summarizer（后端 FastAPI）
- 文档语言：中文
- 目标读者：前端开发者（联调与 SDK 封装）
- 生成日期：2026-05-16
- 路由覆盖统计：43 个（/health 1 个 + 业务路由 42 个）
- 依据源码层级：
  1. 路由注册与 controller：backend/app_factory.py, backend/api/routes/*.py
  2. 请求/响应模型：backend/schemas/*.py, backend/schemas/common.py, backend/auth/models.py
  3. 业务语义补充：.github/skills/phase-driven-implementation/references/项目修改计划.md
- 覆盖范围：create_app() 中实际 include 的全部 router（含 /health）

## 2. 全局协议约定

### 2.1 基础信息
- 版本前缀：/api/v1
- 鉴权方式：Bearer Token（HTTP Authorization）
- 常见 Header：
  - Authorization: Bearer <access_token>
  - x-request-id: 可选，客户端可传；不传由服务端生成
  - x-trace-id: 可选，客户端可传；不传复用 request_id

### 2.1.1 TUS 分片上传 Header（仅 file_upload 路由）
- Tus-Resumable: 1.0.0（PATCH/HEAD 响应会返回；PATCH 请求若提供且非 1.0.0 则 412）
- Upload-Offset: 当前字节偏移（PATCH 请求必传；HEAD/PATCH 响应返回最新 offset）
- Upload-Length: 文件总字节数（HEAD 响应返回）
- Content-Type: application/offset+octet-stream（PATCH 推荐值；当前实现未强校验该值）

### 2.2 时间与命名
- 时间：UTC，ISO 8601，如 2026-05-13T08:30:00Z（来自 MetaInfo）
- 字段命名：snake_case
- ID：当前接口层按字符串处理（计划文档目标为 UUIDv7）

### 2.3 响应信封

#### A. 业务接口（除 auth 与 health）
成功（单对象）
```json
{
  "status": "success",
  "data": {},
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

成功（列表）
```json
{
  "status": "success",
  "data": [],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 0,
    "has_next": false,
    "next_cursor": null
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### B. auth 接口
auth 路由响应模型未包含 meta/pagination，仅返回 status + data。

#### C. 异常返回
- 多数业务错误来自 HTTPException，格式为 FastAPI 默认：
```json
{
  "detail": "..."
}
```
- 全局未捕获异常（500）在 app_factory.py 中为：
```json
{
  "status": "error",
  "message": "Internal Server Error"
}
```

## 3. 鉴权与会话
- 需要登录的接口：除 /health、/api/v1/auth/register、/api/v1/auth/login、/api/v1/auth/refresh 外全部需要 access token。
- get_current_user 鉴权失败返回 401：
  - Missing token
  - Invalid token type
  - token 解码错误

## 4. 路由总览

| 域 | Method | Path | 鉴权 | 成功状态码 | 响应模型 |
|---|---|---|---|---|---|
| system | GET | /health | 否 | 200 | dict[str, str] |
| auth | POST | /api/v1/auth/register | 否 | 200 | CurrentUserResponse |
| auth | POST | /api/v1/auth/login | 否 | 200 | TokenResponse |
| auth | POST | /api/v1/auth/refresh | 否 | 200 | TokenResponse |
| auth | GET | /api/v1/auth/me | 是 | 200 | CurrentUserResponse |
| knowledge-bases | POST | /api/v1/kbs | 是 | 201 | KnowledgeBaseResponse |
| knowledge-bases | GET | /api/v1/kbs | 是 | 200 | KnowledgeBaseListResponse |
| knowledge-bases | GET | /api/v1/kbs/{kbid} | 是 | 200 | KnowledgeBaseResponse |
| knowledge-bases | PATCH | /api/v1/kbs/{kbid} | 是 | 200 | KnowledgeBaseResponse |
| knowledge-bases | DELETE | /api/v1/kbs/{kbid} | 是 | 200 | KnowledgeBaseDeleteResponse |
| knowledge-bases | POST | /api/v1/kbs/{kbid}/videos | 是 | 200 | KnowledgeBaseVideoBindResponse |
| knowledge-bases | GET | /api/v1/kbs/{kbid}/videos | 是 | 200 | KnowledgeBaseVideoListResponse |
| knowledge-bases | DELETE | /api/v1/kbs/{kbid}/videos/{video_id} | 是 | 200 | KnowledgeBaseVideoRemoveResponse |
| video-resources | POST | /api/v1/videos | 是 | 201 | VideoResourceResponse |
| video-resources | GET | /api/v1/videos | 是 | 200 | VideoResourceListResponse |
| video-resources | GET | /api/v1/videos/{video_id} | 是 | 200 | VideoResourceResponse |
| video-resources | PATCH | /api/v1/videos/{video_id} | 是 | 200 | VideoResourceResponse |
| video-resources | DELETE | /api/v1/videos/{video_id} | 是 | 202 | VideoResourceDeleteResponse |
| video-summary-tasks | POST | /api/v1/tasks | 是 | 201 | VideoSummaryTaskResponse |
| video-summary-tasks | GET | /api/v1/tasks | 是 | 200 | VideoSummaryTaskListResponse |
| video-summary-tasks | GET | /api/v1/tasks/{task_id} | 是 | 200 | VideoSummaryTaskResponse |
| video-summary-tasks | PATCH | /api/v1/tasks/{task_id} | 是 | 200 | VideoSummaryTaskResponse |
| video-summary-tasks | DELETE | /api/v1/tasks/{task_id} | 是 | 200 | VideoSummaryTaskDeleteResponse |
| video-qa | POST | /api/v1/tasks/{task_id}/qa | 是 | 201 | VideoQARecordResponse |
| video-qa | GET | /api/v1/tasks/{task_id}/qa | 是 | 200 | VideoQARecordListResponse |
| video-qa | GET | /api/v1/tasks/{task_id}/qa/{qa_id} | 是 | 200 | VideoQARecordResponse |
| video-qa | PATCH | /api/v1/tasks/{task_id}/qa/{qa_id} | 是 | 200 | VideoQARecordResponse |
| video-qa | DELETE | /api/v1/tasks/{task_id}/qa/{qa_id} | 是 | 200 | VideoQARecordDeleteResponse |
| global-chat | POST | /api/v1/kbs/{kbid}/chats | 是 | 201 | GlobalChatSessionResponse |
| global-chat | GET | /api/v1/kbs/{kbid}/chats | 是 | 200 | GlobalChatSessionListResponse |
| global-chat | GET | /api/v1/kbs/{kbid}/chats/{chat_id} | 是 | 200 | GlobalChatSessionResponse |
| global-chat | PATCH | /api/v1/kbs/{kbid}/chats/{chat_id} | 是 | 200 | GlobalChatSessionResponse |
| global-chat | DELETE | /api/v1/kbs/{kbid}/chats/{chat_id} | 是 | 200 | GlobalChatSessionDeleteResponse |
| global-qa | POST | /api/v1/kbs/{kbid}/chats/{chat_id}/qa | 是 | 201 | GlobalQARecordResponse |
| global-qa | GET | /api/v1/kbs/{kbid}/chats/{chat_id}/qa | 是 | 200 | GlobalQARecordListResponse |
| global-qa | GET | /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id} | 是 | 200 | GlobalQARecordResponse |
| global-qa | PATCH | /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id} | 是 | 200 | GlobalQARecordResponse |
| global-qa | DELETE | /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id} | 是 | 200 | GlobalQARecordDeleteResponse |
| devices | POST | /api/v1/devices | 是 | 200 | DeviceRegisterResponse |
| devices | DELETE | /api/v1/devices/{device_token_id} | 是 | 200 | dict（success + message） |
| devices | GET | /api/v1/devices | 是 | 200 | dict（success + data list） |
| video-summary-tasks-workflow | POST | /api/v1/tasks/{task_id}/start-analysis | 是 | 202 | StartAnalysisWorkflowResponse |
| video-summary-tasks-workflow | POST | /api/v1/tasks/{task_id}/approve-and-finalize | 是 | 202 | ApproveAndFinalizeResponse |
| video-summary-tasks-workflow | POST | /api/v1/tasks/{task_id}/time-travel-qa | 是 | 200 | TimeTravelQAResponse |
| file_upload | POST | /api/v1/uploads | 是 | 201 | InitUploadResponse |
| file_upload | HEAD | /api/v1/uploads/{upload_id} | 是 | 204 | Response（TUS headers） |
| file_upload | PATCH | /api/v1/uploads/{upload_id} | 是 | 200/204 | Response（TUS headers） |
| file_upload | DELETE | /api/v1/uploads/{upload_id} | 是 | 200 | UploadCancelResponse（运行时返回 dict） |
| file_upload | GET | /api/v1/uploads/{upload_id} | 是 | 200 | ChunkStatusResponse |

## 5. 公共 JSON 结构说明（前端重点）

### 5.1 MetaInfo
```json
{
  "request_id": "string",
  "timestamp": "YYYY-MM-DDTHH:MM:SSZ"
}
```
- request_id：请求追踪 ID（可用于日志排障）
- timestamp：服务端返回时间（UTC）

### 5.2 PaginationInfo
```json
{
  "page": 1,
  "page_size": 20,
  "total": 100,
  "has_next": true,
  "next_cursor": null
}
```
约束：
- page >= 1
- 1 <= page_size <= 100
- total >= 0

### 5.3 附件结构 AttachmentInfo（VideoQA/GlobalQA）
```json
{
  "name": "screenshot.png",
  "oss_key": "attachments/usr_xxx/qa_xxx/screenshot.png",
  "mime_type": "image/png",
  "size_bytes": 1024
}
```
字段语义（计划文档对齐）：
- name：附件显示名
- oss_key：OSS 对象键（数据库持久化的真实值）
- mime_type：媒体类型
- size_bytes：文件大小，>=0

### 5.4 引用结构 CitedSource（GlobalQA）
```json
{
  "video_id": "vid_001",
  "task_id": "task_001",
  "time_range": "00:10:00-00:11:00",
  "quote": "这里解释了模型训练策略",
  "score": 0.91
}
```
约束：
- score 范围 [0, 1]

## 6. 详细接口定义（逐路由）

### 6.1 system

#### GET /health
- 鉴权：否
- 请求 JSON（等价表达）：
```json
{}
```
- 响应 JSON：
```json
{
  "status": "ok"
}
```

### 6.2 auth

#### POST /api/v1/auth/register
- 鉴权：否
- 请求体字段：
  - username: string, min 3, max 50
  - password: string, min 8, max 128
- 请求示例：
```json
{
  "username": "alice",
  "password": "Secret123!"
}
```
- 成功响应示例：
```json
{
  "status": "success",
  "data": {
    "user_id": "usr_001",
    "username": "alice"
  }
}
```
- 典型错误：
  - 409（用户名冲突）
  - 422（字段校验失败）

#### POST /api/v1/auth/login
- 鉴权：否
- 请求体字段：
  - username: string, min 3, max 50
  - password: string, min 8, max 128
  - device_id: string, min 1, max 128
- 请求示例：
```json
{
  "username": "alice",
  "password": "Secret123!",
  "device_id": "android_001"
}
```
- 成功响应示例：
```json
{
  "status": "success",
  "data": {
    "access_token": "jwt_access",
    "refresh_token": "jwt_refresh",
    "token_type": "bearer",
    "expires_in": 1800,
    "user": {
      "user_id": "usr_001",
      "username": "alice"
    }
  }
}
```
- 典型错误：
  - 401（账号或密码错误）
  - 422（字段校验失败）

#### POST /api/v1/auth/refresh
- 鉴权：否（靠 refresh_token 校验）
- 请求体字段：
  - refresh_token: string, min 20
  - device_id: string, min 1, max 128
- 请求示例：
```json
{
  "refresh_token": "jwt_refresh",
  "device_id": "android_001"
}
```
- 成功响应示例：与 login 相同结构
- 典型错误：
  - 401（refresh token 非法、type 非 refresh、device 不匹配、subject 无效）

#### GET /api/v1/auth/me
- 鉴权：是
- 请求 JSON（等价表达）：
```json
{}
```
- 成功响应示例：
```json
{
  "status": "success",
  "data": {
    "user_id": "usr_001",
    "username": "alice"
  }
}
```
- 典型错误：
  - 401（缺失/非法 access token）
  - 404（token 对应用户不存在）

### 6.3 knowledge-bases

#### POST /api/v1/kbs
- 鉴权：是
- 请求模型：KnowledgeBaseCreateRequest
- 请求示例：
```json
{
  "name": "LLM 研究库",
  "category": "research",
  "description": "收录大模型公开视频",
  "config": {
    "retrieval": {
      "top_k": 5,
      "rerank": true
    },
    "tool_preferences": {
      "allow_web_search": false
    },
    "llm_policy": {
      "temperature": 0.2
    }
  }
}
```
- 成功响应示例：
```json
{
  "status": "success",
  "data": {
    "kbid": "kb_001",
    "owner_id": "usr_001",
    "name": "LLM 研究库",
    "category": "research",
    "description": "收录大模型公开视频",
    "vector_collection_name": "kb_xxx",
    "config": {
      "retrieval": {
        "top_k": 5,
        "rerank": true
      },
      "tool_preferences": {
        "allow_web_search": false
      },
      "llm_policy": {
        "temperature": 0.2
      }
    },
    "created_at": "2026-05-15T12:00:00Z"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### GET /api/v1/kbs
- 鉴权：是
- Query 参数：page/page_size/fields/sort/cursor
- 规则：fields 仅做白名单校验，不改变响应字段集
- 请求 JSON（等价表达）：
```json
{
  "page": 1,
  "page_size": 20,
  "fields": "kbid,name"
}
```
- 成功响应示例：
```json
{
  "status": "success",
  "data": [
    {
      "kbid": "kb_001",
      "owner_id": "usr_001",
      "name": "LLM 研究库",
      "category": "research",
      "description": "收录大模型公开视频",
      "vector_collection_name": "kb_xxx",
      "config": {
        "retrieval": {
          "top_k": 5,
          "rerank": true
        },
        "tool_preferences": {
          "allow_web_search": false
        },
        "llm_policy": {
          "temperature": 0.2
        }
      },
      "created_at": "2026-05-15T12:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 1,
    "has_next": false,
    "next_cursor": null
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### GET /api/v1/kbs/{kbid}
- 鉴权：是
- 请求 JSON（等价表达）：
```json
{
  "kbid": "kb_001"
}
```
- 成功响应：同单对象 KnowledgeBaseResponse
- 典型错误：404 Knowledge base not found

#### PATCH /api/v1/kbs/{kbid}
- 鉴权：是
- 可更新字段：name/category/description/config
- 请求示例：
```json
{
  "name": "LLM 工程库",
  "category": "engineering",
  "description": "更新后的说明",
  "config": {
    "retrieval": {
      "top_k": 8,
      "rerank": true
    },
    "tool_preferences": {
      "allow_web_search": true
    },
    "llm_policy": {
      "temperature": 0.5
    }
  }
}
```
- 成功响应：同单对象 KnowledgeBaseResponse

#### DELETE /api/v1/kbs/{kbid}
- 鉴权：是
- 请求 JSON（等价表达）：
```json
{
  "kbid": "kb_001"
}
```
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "kbid": "kb_001"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### POST /api/v1/kbs/{kbid}/videos
- 鉴权：是
- 请求体：
```json
{
  "video_id": "vid_001"
}
```
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "kbid": "kb_001",
    "video_id": "vid_001"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### GET /api/v1/kbs/{kbid}/videos
- 鉴权：是
- Query 参数：page/page_size
- 成功响应示例：
```json
{
  "status": "success",
  "data": [
    {
      "video_id": "vid_001",
      "file_name": "video.mp4",
      "created_at": "2026-05-15T12:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 1,
    "has_next": false,
    "next_cursor": null
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### DELETE /api/v1/kbs/{kbid}/videos/{video_id}
- 鉴权：是
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "kbid": "kb_001",
    "video_id": "vid_001"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

### 6.4 video-resources

#### POST /api/v1/videos
- 鉴权：是
- 请求体：
```json
{
  "file_name": "intro.mp4"
}
```
约束：1..255，extra=forbid
- 成功响应（字段语义已对齐计划）：
```json
{
  "status": "success",
  "data": {
    "video_id": "vid_001",
    "owner_id": "usr_001",
    "file_name": "intro.mp4",
    "oss_key": "",
    "duration": 0,
    "full_transcript": null,
    "transcribe_status": "UPLOADED",
    "transcript_vector_ids": null,
    "keyframes": null,
    "frame_extraction_status": "UPLOADED",
    "keyframes_oss_prefix": null,
    "extract_completed_at": null,
    "created_at": "2026-05-15T12:00:00Z"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### GET /api/v1/videos
- 鉴权：是
- Query 参数：page/page_size/fields/sort/cursor
- 成功响应：VideoResourceView[] + PaginationInfo + MetaInfo

#### GET /api/v1/videos/{video_id}
- 鉴权：是
- 成功响应：VideoResourceResponse
- 404：Video resource not found

#### PATCH /api/v1/videos/{video_id}
- 鉴权：是
- 仅允许 file_name 更新
- 请求示例：
```json
{
  "file_name": "intro-v2.mp4"
}
```
- 成功响应：VideoResourceResponse

#### DELETE /api/v1/videos/{video_id}
- 鉴权：是
- 成功状态码：202
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "video_id": "vid_001"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```
- 语义（计划对齐）：当前接口返回删除受理语义，后续可扩展异步级联清理流程。

### 6.5 video-summary-tasks

#### POST /api/v1/tasks
- 鉴权：是
- 请求体：
```json
{
  "kbid": "kb_001",
  "video_id": "vid_001",
  "user_initial_preference": "请生成面向产品经理的结构化摘要"
}
```
- 成功响应示例：
```json
{
  "status": "success",
  "data": {
    "task_id": "task_001",
    "kbid": "kb_001",
    "video_id": "vid_001",
    "workflow_state": "DRAFT_GENERATING",
    "user_initial_preference": "请生成面向产品经理的结构化摘要",
    "draft_summary": null,
    "user_guidance": null,
    "final_summary": null,
    "title": null,
    "summary_vector_ids": null,
    "created_at": "2026-05-15T12:00:00Z",
    "updated_at": "2026-05-15T12:00:00Z"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### GET /api/v1/tasks
- 鉴权：是
- Query 参数：page/page_size/fields/sort/cursor
- 成功响应：VideoSummaryTaskView[] + PaginationInfo + MetaInfo

#### GET /api/v1/tasks/{task_id}
- 鉴权：是
- 成功响应：VideoSummaryTaskResponse

#### PATCH /api/v1/tasks/{task_id}
- 鉴权：是
- 可写字段（用户接口）：draft_summary、user_guidance、title
- 请求示例：
```json
{
  "draft_summary": "这是修订后的初稿",
  "user_guidance": "请强调技术架构",
  "title": "视频总结-技术版"
}
```
- 成功响应：VideoSummaryTaskResponse

#### DELETE /api/v1/tasks/{task_id}
- 鉴权：是
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "task_id": "task_001"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

### 6.6 video-qa（单视频局部追问）

#### POST /api/v1/tasks/{task_id}/qa
- 鉴权：是
- 约束：path.task_id 必须等于 body.task_id
- 请求体：
```json
{
  "task_id": "task_001",
  "start_time": "00:10:00",
  "end_time": "00:12:00",
  "question_content": "这段视频讲的是什么?",
  "attachments": [
    {
      "name": "screenshot.png",
      "oss_key": "attachments/usr_001/qa_001/screenshot.png",
      "mime_type": "image/png",
      "size_bytes": 102400
    }
  ]
}
```
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "qa_id": "qa_001",
    "task_id": "task_001",
    "start_time": "00:10:00",
    "end_time": "00:12:00",
    "question_content": "这段视频讲的是什么?",
    "answer_content": null,
    "attachments": [
      {
        "name": "screenshot.png",
        "oss_key": "attachments/usr_001/qa_001/screenshot.png",
        "mime_type": "image/png",
        "size_bytes": 102400
      }
    ],
    "question_time": "2026-05-15T12:00:00Z"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### GET /api/v1/tasks/{task_id}/qa
- 鉴权：是
- Query：page/page_size/fields/sort/cursor
- 成功响应：VideoQARecordView[] + PaginationInfo + MetaInfo

#### GET /api/v1/tasks/{task_id}/qa/{qa_id}
- 鉴权：是
- 成功响应：VideoQARecordResponse

#### PATCH /api/v1/tasks/{task_id}/qa/{qa_id}
- 鉴权：是
- 请求体仅允许：
```json
{
  "regenerate": true
}
```
- 当前实现语义：仅“重生成意图”校验与占位返回，不在此接口内直接写入新回答文本。
- 成功响应：VideoQARecordResponse

#### DELETE /api/v1/tasks/{task_id}/qa/{qa_id}
- 鉴权：是
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "qa_id": "qa_001"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

### 6.7 global-chat（知识库全局会话）

#### POST /api/v1/kbs/{kbid}/chats
- 鉴权：是
- 约束：path.kbid 必须等于 body.kbid
- 请求体：
```json
{
  "kbid": "kb_001",
  "chat_title": "研发周会问答"
}
```
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "chat_id": "chat_001",
    "kbid": "kb_001",
    "chat_title": "研发周会问答",
    "created_at": "2026-05-15T12:00:00Z"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### GET /api/v1/kbs/{kbid}/chats
- 鉴权：是
- Query：page/page_size/fields/sort/cursor
- 成功响应：GlobalChatSessionView[] + PaginationInfo + MetaInfo

#### GET /api/v1/kbs/{kbid}/chats/{chat_id}
- 鉴权：是
- 成功响应：GlobalChatSessionResponse

#### PATCH /api/v1/kbs/{kbid}/chats/{chat_id}
- 鉴权：是
- 请求体：
```json
{
  "chat_title": "新的会话标题"
}
```
- 成功响应：GlobalChatSessionResponse

#### DELETE /api/v1/kbs/{kbid}/chats/{chat_id}
- 鉴权：是
- 语义：删除会话前会先删除该会话下所有 GlobalQA 记录
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "chat_id": "chat_001"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

### 6.8 global-qa（跨文档问答）

#### POST /api/v1/kbs/{kbid}/chats/{chat_id}/qa
- 鉴权：是
- 请求体：
```json
{
  "question_content": "请对比两个视频中的架构差异",
  "attachments": [
    {
      "name": "diagram.png",
      "oss_key": "attachments/usr_001/qa_001/diagram.png",
      "mime_type": "image/png",
      "size_bytes": 12000
    }
  ]
}
```
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "qa_id": "gqa_001",
    "chat_id": "chat_001",
    "question_content": "请对比两个视频中的架构差异",
    "answer_content": null,
    "attachments": [
      {
        "name": "diagram.png",
        "oss_key": "attachments/usr_001/qa_001/diagram.png",
        "mime_type": "image/png",
        "size_bytes": 12000
      }
    ],
    "cited_sources": [],
    "question_time": "2026-05-15T12:00:00Z"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

#### GET /api/v1/kbs/{kbid}/chats/{chat_id}/qa
- 鉴权：是
- Query：page/page_size/fields/sort/cursor
- 成功响应：GlobalQARecordView[] + PaginationInfo + MetaInfo

#### GET /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}
- 鉴权：是
- 成功响应：GlobalQARecordResponse

#### PATCH /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}
- 鉴权：是
- 请求体：
```json
{
  "regenerate": true
}
```
- 当前实现语义：仅“重生成意图”校验与占位返回，不在此接口内直接生成新 answer_content。
- 成功响应：GlobalQARecordResponse

#### DELETE /api/v1/kbs/{kbid}/chats/{chat_id}/qa/{qa_id}
- 鉴权：是
- 成功响应：
```json
{
  "status": "success",
  "data": {
    "qa_id": "gqa_001"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```

### 6.8 devices（FCM 设备令牌管理）

#### POST /api/v1/devices
- 鉴权：是
- 请求体字段：
  - device_token: string, min 1, max 512（FCM 设备令牌）
  - platform: string, enum=["iOS", "Android"]
  - app_version: string, min 1, max 50
  - device_id: string, min 1, max 128
- 请求示例：
```json
{
  "device_token": "cMsNDjLxE8I:APA91bFhHEWj7...",
  "platform": "Android",
  "app_version": "1.0.0",
  "device_id": "android_device_001"
}
```
- 成功响应（200，upsert 语义 - 相同 device_token 则更新）：
```json
{
  "status": "success",
  "data": {
    "device_token_id": "dtid_001",
    "user_id": "usr_001",
    "device_token": "cMsNDjLxE8I:APA91bFhHEWj7...",
    "platform": "Android",
    "app_version": "1.0.0",
    "device_id": "android_device_001",
    "created_at": "2026-05-15T12:00:00Z",
    "updated_at": "2026-05-15T12:00:00Z"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```
- 字段语义：
  - device_token_id：设备令牌记录唯一 ID
  - device_token：FCM 返回的推送令牌
  - platform：设备操作系统（iOS/Android）
  - app_version：应用版本号（前端上报）
  - device_id：设备唯一标识符（供前端追踪）

#### DELETE /api/v1/devices/{device_token_id}
- 鉴权：是（必须是令牌所有者）
- 请求示例（path 参数）：
```json
{
  "device_token_id": "dtid_001"
}
```
- 成功响应（200）：
```json
{
  "status": "success",
  "message": "设备令牌已注销"
}
```
- 典型错误：
  - 404：设备令牌不存在
  - 403：无权限删除他人的设备令牌（权限检查基于 user_id）

#### GET /api/v1/devices
- 鉴权：是
- Query 参数：无
- 请求示例：
```json
{}
```
- 成功响应（200，返回当前用户的所有已注册设备）：
```json
{
  "status": "success",
  "data": [
    {
      "device_token_id": "dtid_001",
      "user_id": "usr_001",
      "device_token": "cMsNDjLxE8I:APA91bFhHEWj7...",
      "platform": "Android",
      "app_version": "1.0.0",
      "device_id": "android_device_001",
      "created_at": "2026-05-15T12:00:00Z",
      "updated_at": "2026-05-15T12:00:00Z"
    },
    {
      "device_token_id": "dtid_002",
      "user_id": "usr_001",
      "device_token": "APA91bG...xyz",
      "platform": "iOS",
      "app_version": "1.0.2",
      "device_id": "ios_device_001",
      "created_at": "2026-05-15T11:00:00Z",
      "updated_at": "2026-05-15T11:00:00Z"
    }
  ]
}
```
- 字段语义：同 POST 响应

### 6.9 file_upload（TUS 分片上传）

#### POST /api/v1/uploads
- 鉴权：是
- 请求体模型：InitUploadRequest
- 字段约束：
  - file_name: string, min 1, max 512
  - total_size: int, gt 0, le 10737418240（10GB）
- 请求示例：
```json
{
  "file_name": "demo.mp4",
  "total_size": 31457280
}
```
- 成功响应（201）：
```json
{
  "upload_id": "1f496d4a-e9f9-4f5b-a1d7-4d78429b17f6",
  "chunk_size": 10485760,
  "expires_at": "2026-05-17T12:00:00+00:00"
}
```

#### HEAD /api/v1/uploads/{upload_id}
- 鉴权：是
- 请求 JSON（等价表达）：
```json
{
  "upload_id": "1f496d4a-e9f9-4f5b-a1d7-4d78429b17f6"
}
```
- 成功响应：204，无 body；通过 header 返回进度
  - Tus-Resumable: 1.0.0
  - Upload-Offset: 0
  - Upload-Length: 31457280
  - Cache-Control: no-store

#### PATCH /api/v1/uploads/{upload_id}
- 鉴权：是
- 请求头：
  - Upload-Offset: int（必填）
  - Tus-Resumable: 可选；若传且非 1.0.0 返回 412
- 请求体：二进制分片（application/offset+octet-stream）
- 服务器分片规则：固定 10 MiB（10485760 字节）
- 请求示例（伪 JSON 等价表达，二进制体用占位）：
```json
{
  "upload_id": "1f496d4a-e9f9-4f5b-a1d7-4d78429b17f6",
  "headers": {
    "Upload-Offset": 0,
    "Tus-Resumable": "1.0.0",
    "Content-Type": "application/offset+octet-stream"
  },
  "body": "<binary_chunk_bytes>"
}
```
- 未完成时响应：204，无 body
  - Tus-Resumable: 1.0.0
  - Upload-Offset: <最新 uploaded_size>
- 全部分片完成时响应：200，无 body
  - Tus-Resumable: 1.0.0
  - Upload-Offset: <total_size>

#### DELETE /api/v1/uploads/{upload_id}
- 鉴权：是
- 请求 JSON（等价表达）：
```json
{
  "upload_id": "1f496d4a-e9f9-4f5b-a1d7-4d78429b17f6"
}
```
- 成功响应（200）：
```json
{
  "upload_id": "1f496d4a-e9f9-4f5b-a1d7-4d78429b17f6",
  "status": "cancelled"
}
```

#### GET /api/v1/uploads/{upload_id}
- 鉴权：是
- 用途：查询上传进度（JSON 轮询接口，非 TUS 标准）
- 请求 JSON（等价表达）：
```json
{
  "upload_id": "1f496d4a-e9f9-4f5b-a1d7-4d78429b17f6"
}
```
- 成功响应（200）：
```json
{
  "upload_id": "1f496d4a-e9f9-4f5b-a1d7-4d78429b17f6",
  "uploaded_size": 10485760,
  "total_size": 31457280,
  "uploaded_chunks": [0]
}
```
- 字段语义：
  - upload_id：上传会话 ID（当前实现为 UUID4 字符串）
  - uploaded_size：已完成分片累计字节
  - total_size：文件总字节
  - uploaded_chunks：已完成分片索引（升序）

### 6.11 video-summary-tasks-workflow（工作流两阶段执行与时间旅行 QA）

#### POST /api/v1/tasks/{task_id}/start-analysis
- 鉴权：是
- 用途：启动分析阶段（对应 LangGraph workflow 的 analysis 节点）；异步执行，立即返回 202
- 请求体：
```json
{
  "task_id": "task_001"
}
```
空 body（task_id 来自路径）
- 成功响应（202 Accepted，表示异步任务已受理）：
```json
{
  "status": "success",
  "data": {
    "task_id": "task_001",
    "workflow_state": "DRAFT_GENERATING",
    "message": "分析阶段已启动，请通过 WebSocket 监听进度"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```
- 字段语义：
  - workflow_state：工作流当前状态（DRAFT_GENERATING = 分析中）
  - message：业务提示信息
- 异步行为：
  - 触发 Celery 异步任务 `async_execute_analysis_workflow`
  - 前端需通过 WebSocket 监听 ProgressPublishService 的事件以获取实时进度
  - 进度事件格式：{ type: "progress", scope: "video_summary_task", scope_id: task_id, progress: 0-100 }
- 典型错误：
  - 404：任务不存在
  - 409：任务当前状态不允许启动分析（预期 DRAFT_GENERATING）
  - 400：转录或关键帧数据缺失

#### POST /api/v1/tasks/{task_id}/approve-and-finalize
- 鉴权：是
- 用途：用户审批分析结果并启动最终化阶段（对应 LangGraph workflow 的 finalization 节点）；异步执行，立即返回 202
- 请求体字段：
  - edited_aggregated_chunk_insights: string, max 20000（可选，用户对聚合摘要的编辑/反馈）
  - human_guidance: string, max 10000（可选，对最终化过程的人工指导）
- 请求示例：
```json
{
  "edited_aggregated_chunk_insights": "更新后的核心要点：...",
  "human_guidance": "请特别关注第二部分的结论"
}
```
- 成功响应（202 Accepted）：
```json
{
  "status": "success",
  "data": {
    "task_id": "task_001",
    "workflow_state": "FINAL_GENERATING",
    "message": "最终化阶段已启动，请通过 WebSocket 监听进度"
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```
- 字段语义：
  - workflow_state：工作流当前状态（FINAL_GENERATING = 最终化中）
- 异步行为：
  - 触发 Celery 异步任务 `async_execute_finalization_workflow`
  - 前端需通过 WebSocket 监听进度
  - 完成后事件格式：{ type: "completed", scope: "video_summary_task", scope_id: task_id, data: {...} }
  - 失败后事件格式：{ type: "error", scope: "video_summary_task", scope_id: task_id, error: "..." }
- 典型错误：
  - 404：任务不存在
  - 409：任务当前状态不允许最终化（预期 WAITING_USER_APPROVAL）

#### POST /api/v1/tasks/{task_id}/time-travel-qa
- 鉴权：是
- 用途：基于检查点进行时间旅行 QA；同步执行，立即返回 200
- 请求体字段：
  - timestamp: string, pattern=HH:MM:SS（视频时间点）
  - question: string, min 1, max 5000（追问内容）
  - window_seconds: integer, min 5, max 300（时间窗口，秒数）
- 请求示例：
```json
{
  "timestamp": "00:10:30",
  "question": "这部分讲的核心观点是什么？",
  "window_seconds": 60
}
```
- 成功响应（200 OK，同步返回）：
```json
{
  "status": "success",
  "data": {
    "task_id": "task_001",
    "timestamp": "00:10:30",
    "question": "这部分讲的核心观点是什么？",
    "answer": "这部分主要介绍了...",
    "checkpoint_available": true,
    "references": [
      {
        "frame_index": 315,
        "oss_key": "keyframes/task_001/frame_315.jpg",
        "confidence": 0.92
      }
    ]
  },
  "meta": {
    "request_id": "req-123",
    "timestamp": "2026-05-15T12:00:00Z"
  }
}
```
- 字段语义：
  - timestamp：请求时的视频时间点
  - question：用户提出的追问
  - answer：基于 LangGraph 检查点生成的回答
  - checkpoint_available：当前 workflow state 是否存在可用检查点
  - references：关键帧或转录片段引用列表
- 前置条件：
  - 任务必须处于 WAITING_USER_APPROVAL、FINAL_GENERATING 或 COMPLETED 状态之一
  - 需存在有效的 LangGraph 检查点（state checkpoint）
- 典型错误：
  - 404：任务不存在
  - 409：任务当前状态不允许时间旅行 QA（预期上述三种状态之一）
  - 400：时间戳格式非法或超出视频时长范围
  - 503：检查点数据不可用

### 6.12 FCM 推送事件映射（系统通知，非 HTTP 接口）
- **触发方**：WorkflowNotificationService（backend/services/notification.py）
- **映射规则**：
  - WorkflowEvent.COMPLETED → FCM title="摘要生成完成", body="您的视频总结已就绪", data.deep_link="/summary/{task_id}"
  - WorkflowEvent.FAILED → FCM title="摘要生成失败", body="生成过程中出错，请重试", data.deep_link="/task/{task_id}"
  - WorkflowEvent.APPROVAL_REQUIRED → FCM title="需要您的审批", body="分析完成，待您确认后启动最终化", data.deep_link="/approval/{task_id}"
- **数据结构**：FCMPushPayload { title, body, data: { scope: "video_summary_task", scope_id: task_id, deep_link } }

## 7. 字段语义说明（按业务对象）

### 7.1 KnowledgeBase.config
```json
{
  "retrieval": {
    "top_k": 5,
    "rerank": true
  },
  "tool_preferences": {
    "allow_web_search": false
  },
  "llm_policy": {
    "temperature": 0.2
  }
}
```
- retrieval.top_k：检索召回数量（>=1）
- retrieval.rerank：是否启用重排
- tool_preferences.allow_web_search：是否允许联网检索
- llm_policy.temperature：生成温度（0~2）

### 7.2 VideoResource 关键字段
- transcribe_status：转录状态（UPLOADED/TRANSCRIBING/EXTRACTING/COMPLETED/FAILED）
- frame_extraction_status：抽帧状态（同上枚举）
- keyframes[].oss_key：关键帧在 OSS 的对象键
- extract_completed_at：资源（转录+抽帧）就绪时间
- transcript_vector_ids：转录分块向量 ID 列表

### 7.3 QA 相关字段
- start_time/end_time：局部追问命中的视频时间区间（字符串）
- attachments：附件元数据数组（持久化主键为 oss_key，不传二进制）
- cited_sources：回答证据链数组，用于前端引用跳转

### 7.4 Upload（分片上传）字段
- file_name：客户端原始文件名；用于展示与后续默认对象键生成
- total_size：客户端声明的完整文件字节数（上限 10GB）
- chunk_size：服务端固定分片大小（10 MiB），前端应以此值切片
- expires_at：上传会话过期时间（UTC ISO 8601）
- upload_id：上传会话标识（当前运行时由 uuid4 生成）
- uploaded_size：已上传总字节数（用于断点续传偏移）
- uploaded_chunks：已完成分片索引列表（用于前端校验丢片/重传）

### 7.5 工作流状态转移（VideoSummaryTask）
工作流状态及其含义：
- **DRAFT_GENERATING**：分析阶段执行中（调用 POST /api/v1/tasks/{task_id}/start-analysis 后）
- **WAITING_USER_APPROVAL**：分析完成，等待用户审批（分析阶段完成后的中间态）
- **FINAL_GENERATING**：最终化阶段执行中（调用 POST /api/v1/tasks/{task_id}/approve-and-finalize 后）
- **COMPLETED**：工作流完成，摘要生成成功
- **FAILED**：工作流失败（任一阶段异常终止）

允许的状态转移：
- DRAFT_GENERATING → WAITING_USER_APPROVAL（分析完成）
- DRAFT_GENERATING → FAILED（分析失败）
- WAITING_USER_APPROVAL → FINAL_GENERATING（用户触发最终化）
- WAITING_USER_APPROVAL → FAILED（用户未在规定时间内审批或手动取消）
- FINAL_GENERATING → COMPLETED（最终化完成）
- FINAL_GENERATING → FAILED（最终化失败）

### 7.6 时间旅行 QA 检查点（Checkpoint）
- **定义**：LangGraph workflow 的中间执行状态快照，支持从特定时间点重新执行
- **可用条件**：任务状态为 WAITING_USER_APPROVAL、FINAL_GENERATING 或 COMPLETED
- **时间戳格式**：HH:MM:SS（例：00:10:30）
- **时间窗口**：window_seconds 范围 [5, 300]，表示围绕目标时间点的上下文范围
- **答案生成**：基于检查点重新加载转录+关键帧，通过 LLM 针对时间窗口内的内容生成追问回答

## 8. 错误码与状态映射

| 场景 | HTTP | 结构 | 说明 |
|---|---|---|---|
| 鉴权缺失/无效 | 401 | {"detail": "..."} | get_current_user / refresh 校验失败 |
| 资源不存在 | 404 | {"detail": "..."} | owner 校验失败或 ID 不存在 |
| 参数不一致 | 400 | {"detail": "..."} | 如 path 与 body 的 task_id/kbid 不一致 |
| 上传分片非法 | 400 | {"detail": "..."} | 空分片、分片大小不符、会话状态非法、offset 推导出的 chunk_index 非法 |
| TUS 版本不匹配 | 412 | {"detail": "..."} | PATCH 请求 Tus-Resumable 非 1.0.0 |
| 业务冲突 | 409 | {"detail": "..."} | register 用户名重复 |
| 模型校验失败 | 422 | FastAPI validation | 字段缺失/越界/extra 字段 |
| 未捕获异常 | 500 | {"status":"error","message":"Internal Server Error"} | 全局异常处理 |

## 9. 一致性检查报告（route-schema-plan）

### 9.1 覆盖性
- 已覆盖 app_factory.py 中注册的全部 11 组路由（含 system /health、devices、video-summary-tasks-workflow、file_upload）。
- 已覆盖所有 controller 路径、方法、响应模型。
- 新增覆盖（步骤 6.5-6.8）：
  - devices 模块：POST/DELETE/GET 三个设备令牌管理端点
  - video-summary-tasks-workflow 模块：POST /start-analysis、POST /approve-and-finalize、POST /time-travel-qa 三个工作流端点

### 9.2 已确认一致项
- 路由前缀、路径、状态码与 response_model 对齐。
- 列表接口分页结构统一为 PaginationInfo。
- fields 参数均执行白名单校验，非法字段返回 400。
- 工作流异步接口（start-analysis、approve-and-finalize）返回 202 Accepted，符合异步任务契约。
- 设备管理接口 DELETE/GET 不使用分页（小集合接口）。

### 9.3 差异与歧义（明确记录）
1. auth 响应未使用 common.MetaInfo 信封（与多数业务接口不同）。
2. 计划文档定义了 ErrorResponse（含 code/is_retryable/retry_after），但当前大部分错误走 FastAPI detail 结构。
3. GET 列表接口的 fields 目前仅校验，不做响应字段裁剪。
4. /api/v1/kbs/{kbid}/chats/{chat_id}/qa 路由函数中存在 `_ = kbid` 赋值，但实际 owner/kbid 校验在 service 层执行。
5. QA 的 PATCH regenerate 目前为“意图占位”，不在接口内直接回写新回答。
6. upload schema 注释声明 upload_id 语义为 UUIDv7，但运行时代码 `UploadService.initiate_upload()` 当前使用 `uuid.uuid4()` 生成；文档已按运行时行为记录。
7. file_upload 路由响应未使用 `SuccessResponse/ErrorResponse` 信封，而是直接返回 JSON DTO 或空 body + TUS headers。
8. devices 路由 GET 返回 data 数组而不是列表信封（无 pagination/meta），DELETE 返回简单 success/message 结构。
9. 工作流状态机和检查点概念由计划文档定义，实现由 LangGraph workflow 支撑；前端需通过 WebSocket 监听异步事件。

## 10. 前端联调建议（基于当前实现）
1. 统一拦截 401/404/422/500，优先兼容 FastAPI detail 结构。
2. 不要假设所有接口都有 meta；auth 系列响应结构不同。
3. 对 fields 参数只作为校验用途，不要依赖其实现响应裁剪。
4. DELETE /api/v1/videos/{video_id} 需按 202 受理语义处理，前端可做异步刷新。
5. 对 QA regenerate 流程，当前应视为触发意图，不应假定立即得到新 answer_content。
6. 对分片上传建议优先按 HEAD 返回的 `Upload-Offset` 做断点续传，并在 PATCH 完成后以 GET/HEAD 双通道校验进度一致性。
7. **设备管理**：登录后立即调用 POST /api/v1/devices 上报设备令牌，用于接收 FCM 推送；app 后台时定期调用 GET /api/v1/devices 以同步设备列表状态。
8. **工作流异步处理**：
   - POST /api/v1/tasks/{task_id}/start-analysis 返回 202 后，前端需建立 WebSocket 连接监听 scope="video_summary_task", scope_id={task_id} 的事件。
   - 监听事件类型：progress（进度更新，0-100）、completed（分析完成，转入审批态）、error（异常终止）。
   - 完成后通过 GET /api/v1/tasks/{task_id} 查询任务状态以获取最新的 aggregated_chunk_insights 和其他元数据。
9. **时间旅行 QA**：
   - POST /api/v1/tasks/{task_id}/time-travel-qa 支持同步调用（200），需任务处于 WAITING_USER_APPROVAL/FINAL_GENERATING/COMPLETED 状态。
   - 时间戳格式必须为 HH:MM:SS，范围需 [0, 视频时长]；window_seconds 建议 60-120 为最佳平衡。
10. **FCM 推送**：接收 FCM 消息后检查 data.scope 和 data.scope_id，根据 deep_link 字段路由到对应页面（/summary/{task_id}、/approval/{task_id} 等）。
