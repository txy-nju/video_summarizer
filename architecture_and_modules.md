# Video Summarizer 后端架构与模块梳理

本项目采用现代化的前后端分离架构，原有的 Streamlit 界面层已废弃。当前后端系统基于 **FastAPI + Celery + Event-Driven (Redis Streams)** 的高可用、高扩展架构构建，负责管理复杂的多模态大模型工作流调度、视频处理、RAG (检索增强生成) 检索与实时状态推送。

## 一、 核心架构设计

系统核心架构采用了标准的分层架构模式 (Layered Architecture) 结合事件驱动设计：

1. **异步任务与事件驱动 (Event-Driven & Async Tasks)**
   - 核心重负载逻辑（如：视频下载、抽帧、Whisper转录、大模型工作流执行）全面转移至 **Celery 异步任务队列**。
   - 采用 **Domain Event Bus (领域事件总线)** 设计。例如，文件上传完成后，通过 Redis Streams 派发 `VideoUploadedEvent`，由 Celery Worker 监听并自动触发后台的“转录与抽帧”并行任务。
2. **LangGraph 工作流与后台驻留**
   - 多智能体工作流（两阶段总结、Self-RAG、HITL）与具体 HTTP 接口解耦。工作流的生命周期由 Celery Task 托管执行（如 `workflow_runtime_tasks.py`）。
   - 工作流执行进度通过 `progress_event_bus` 发布，经由 WebSocket 向前端进行毫秒级的流式状态透传。
3. **WebSocket 与长连接实时通信**
   - 由于大模型推理和视频处理耗时较长，系统引入 WebSocket 进行通信（`backend/websocket/`），配合连接管理器和可能的 Redis Pub/Sub 提供分布式的进度条推送与流式回答。
4. **全面的可观测性 (Observability)**
   - 深度集成了 OpenTelemetry (OTEL)。FastAPI 提供 OTLP 访问日志，Celery 集成了 Task Trace Hooks，支持 Jaeger 端到端的全链路追踪（Tracing）。

---

## 二、 后端系统模块与目录结构

后端的模块化严格遵循职责单一原则，主要功能汇聚在 `backend/` 目录下：

### 1. 接入层 (API & WebSockets)
- **`backend/main.py` & `backend/app_factory.py`**：FastAPI 应用的入口与组装工厂。负责注册所有路由、中间件与事件生命周期。
- **`backend/api/routes/`**：RESTful API 路由模块，依据业务领域垂直拆分。
  - `auth_routes`：认证授权。
  - `video_resource_routes` & `file_upload` / `attachment_upload`：视频与文件资源管理。
  - `video_summary_task_routes`：长视频工作流调度。
  - `video_qa_routes` & `global_qa_routes`：单视频 Time Travel 问答与跨视频的全局 RAG 问答。
  - `kb_routes`：知识库 (Knowledge Base) 管理接口。
  - `global_chat_routes`：全局对话端点。
- **`backend/websocket/`**：`handlers` 配合长连接管理器 (Connection Manager)，主要用于广播工作流进度以及大模型回答的流式 Token。

### 2. 中间件与可观测性 (Middleware & Observability)
- **`backend/middleware/`**：
  - `access_log.py` / `error_handler.py`：统一的访问日志和全局异常捕获。
  - `request_context.py`：利用 contextvars 存储请求级的上下文信息（如 request_id / user_id）。
  - `otel_middleware.py`：拦截 HTTP 请求进行分布式追踪。
- **`backend/observability/tracing.py`**：OTLP/Jaeger 追踪器的初始化，负责跨组件（FastAPI -> Celery -> LangGraph）的上下文连缀。

### 3. 服务逻辑层 (Services)
- **`backend/services/`**：封装所有的核心业务逻辑，串联领域模型和任务系统。
  - **领域事件机制**：`domain_event_bus.py` 和 `domain_event_listener.py` 负责在 Redis Streams 上发布与订阅系统级事件。
  - **状态与进度**：`progress_publish_service.py` 和 `progress_event_bus.py` 将底层处理状态分发给 WebSocket 客户端；`task_status_service.py` 维护任务状态机。
  - **业务调度**：`video_resource_service.py`、`video_summary_task_service.py`、`workflow_orchestration_service.py`。
  - **大模型与 RAG**：`rag_agent_service.py` 和 `video_qa_service.py`、`global_chat_service.py`，实现多模态上下文管理、向量检索以及 Agent 任务执行。

### 4. 异步任务层 (Celery Tasks)
- **`backend/tasks/`**：依托 Celery 承载高计算、高 I/O 延迟任务。
  - `celery_app.py`：Celery Worker 配置，注册 tracing hooks 与消息格式。
  - `transcribe_tasks.py` & `extract_keyframes_tasks.py`：负责底层 Whisper 转录和 OpenCV 智能抽帧的异步执行。
  - `video_summary_tasks.py` & `workflow_runtime_tasks.py`：驱动 LangGraph 的节点流转（Phase 1 和 Phase 2）。
  - `vector_tasks.py` & `global_retrieval_tasks.py`：处理向量化嵌入 (Embeddings) 与跨视频知识库的构建任务。

### 5. 数据持久化层 (DB, Models, Repositories)
- **`backend/db/`** & **`alembic/`**：SQLAlchemy 引擎配置，会话管理及数据库版本控制 (Alembic Migrations)。
- **`backend/models/`**：定义 SQLAlchemy ORM 数据模型。
- **`backend/schemas/`**：Pydantic 模型，负责 API 的入参验证与出参序列化 (Schema)。
- **`backend/repositories/`**：仓储层，将数据持久化的具体 SQL 操作收口，向上游 Service 提供面向对象的数据访问接口。

### 6. 底层依赖 (`core/`)
虽然位于 `backend` 目录之外，但 `core/workflow/` 和 `core/extraction/` 仍是整个应用的引擎，`backend/tasks/` 会直接调用这些核心算子（如基于 LangGraph 的图编排和具体的多模态数据提取）。

---

## 三、 典型数据流动路径 (以视频总结为例)

1. **上传与事件派发**：
   用户通过 `video_resource_routes` 提交文件 -> `upload_service` 处理落盘 -> 往 DB 记录资源 -> 利用 `domain_event_bus` 推送 `VideoUploadedEvent`。
2. **底层并行提取**：
   Celery 守护线程的 `domain_event_listener` 监听到事件 -> 派发并行的 Celery Tasks (`extract_keyframes_tasks` / `transcribe_tasks`)。提取进度通过 `progress_event_bus` 持续推送给用户。
3. **触发工作流**：
   用户调用 `video_summary_task_routes` 发起总结请求 -> `workflow_orchestration_service` 创建相关 Task 模型 -> 启动 `workflow_runtime_tasks`。
4. **LangGraph 调度执行**：
   Celery 进程内执行 `core/workflow` 的大模型图。节点每一次流转（如音频分析、视觉分析完成）都会回调 `task_status_service` 和 `progress_publish_service`。
5. **人类在环 (HITL) 与长连接反馈**：
   阶段一结束后，工作流挂起，数据库记录为 `pending_review` 状态；前端通过 WebSocket 接收到挂起通知，用户在前端审批并通过 API 调用 `workflow_orchestration_service` 的 finalize 接口唤醒工作流 Phase 2。最终完结并落库。
