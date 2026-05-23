# 项目技术亮点清单（多模态视频智能总结系统）

> 从 DEV_SPEC 与源码提炼，供简历编写时按需选取。每个亮点附带"简历话术方向"和"可量化角度"。

---

## 亮点 1：Map-Reduce 分片并行架构

**技术要点**：
- 设计并实现基于 LangGraph 的"分片规划 → 并行分析 → 聚合融合"Map-Reduce 架构
- chunk_planner_node 按时间轴将长视频切分为多个 chunk（默认 120s/片），每个 chunk 含关联的 keyframe_indexes 和 transcript_segment_indexes
- 分片规划采用两指针扫描 + 堆优化，高效完成关键帧/语音片段与时间窗的关联匹配
- 音频分析与视觉分析在图级别并行执行（fan-out），通过自定义 Reducer `_merge_chunk_results` 实现深度合并（fan-in）
- Reducer 实现按 chunk_id 索引的深度合并语义：base 值优先、嵌套 dict 递归合并、顺序保持
- 聚合阶段按 chunk_plan 顺序拼接证据，含截断保护（AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS = 24000）

**简历话术方向**：
- "设计并实现基于 LangGraph StateGraph 的 Map-Reduce 分片并行架构，将长视频按时间轴切分后并行执行音频/视觉双通道分析，自定义 Reducer 实现并行分支状态深度合并"
- "通过两指针扫描 + 堆优化的分片规划算法，将关键帧和语音片段高效关联至时间窗，支撑分钟级粒度的精准分片"

**可量化角度**：分片处理并行度、单视频分片数、端到端处理耗时、聚合上下文完整度

---

## 亮点 2：多模态联合理解（Audio + Vision 双通道）

**技术要点**：
- 同时利用 Whisper 语音转录（verbose_json 保留时间戳分段）和 Vision LLM 关键帧分析，实现音视频双通道联合理解
- 音频分析节点提取分片内语音文本 → LLM 摘要 → 可选 Tavily 搜索增强（自动识别缩写/热词/生僻术语）
- 视觉分析节点提取分片内关键帧 → Vision Model 多图分析 → 可选 Tavily 搜索增强（识别未知 UI/图表/梗图）
- 分片融合节点将 audio_insights 与 vision_insights 对齐融合为 chunk_summary
- 支持 inline base64 和 frame_file 引用两种关键帧加载策略，后者可显著降低 Checkpoint 体积

**简历话术方向**：
- "设计音视频双通道多智能体架构，Whisper 转录文本 + Vision LLM 分析关键帧并行处理，在融合阶段实现跨模态图文对齐"
- "引入 ReAct 模式主动求知，分析节点遇到生僻术语或未知视觉元素时自动调用 Tavily 搜索补充背景知识"

**可量化角度**：多模态通道数、关键帧分析量、搜索增强命中率、跨模态融合覆盖率

---

## 亮点 3：Self-RAG 双重质检闭环

**技术要点**：
- 设计"先查事实、再查需求"的两级质检闭环，杜绝"有文笔但不靠谱"的输出
- 幻觉核查（Hallucination Grader）：JSON Mode + temperature=0 严格验证总结是否脱离原始证据，检出幻觉则生成定向修正指令回流 Fusion Drafter
- 有用性核查（Usefulness Grader）：验证总结是否命中用户指定需求 + human_guidance，偏题则回流重写
- 双重质检共享 `SELF_RAG_MAX_REVISIONS` 熔断参数（.env 可配置），防止质检-重写死循环
- 条件路由通过 LangGraph Conditional Edges 实现，路由常量消灭魔法字符串

**简历话术方向**：
- "实现 Self-RAG 双重质检闭环（幻觉核查 + 有用性核查），基于 JSON Mode 严格评分，不通过则携带修正指令回流重写，并设置熔断阈值防止死循环"
- "质检节点采用 temperature=0 保证评分确定性，LangGraph 条件路由实现回流决策，SELF_RAG_MAX_REVISIONS 熔断保障系统活性"

**可量化角度**：幻觉检出率、重写次数分布、质检通过率、最终总结事实一致性

---

## 亮点 4：LangGraph 状态图工作流引擎

**技术要点**：
- 基于 LangGraph StateGraph 构建两个独立的编译图：Phase 1 分析图（分片并行 + 聚合 + 人审）和 Phase 2 终稿图（成文 + 质检闭环）
- VideoSummaryState（TypedDict）作为全局状态载体，贯穿 10+ 节点，字段覆盖输入/分片/分析/聚合/质检/HITL/可观测性
- 自定义 Reducer（`_merge_chunk_results`）解决并行分支状态合并问题，无 dataclass/pydantic 依赖
- 流式执行（`stream_mode="updates"`）实现节点级事件透传
- 条件边（Conditional Edges）实现质检回流、HITL 审批分支、并发模式路由

**简历话术方向**：
- "基于 LangGraph StateGraph 设计两阶段状态图工作流，10+ 节点通过条件路由、并行分支和自定义 Reducer 实现复杂的多智能体协作"
- "采用 TypedDict + Annotated Reducer 实现轻量级状态管理，自定义深度合并策略解决并行分支数据冲突"

**可量化角度**：工作流节点数、条件路由分支数、状态字段数、双图协作链路长度

---

## 亮点 5：场景感知智能抽帧

**技术要点**：
- 替代传统固定间隔抽帧，基于灰度直方图相关系数（cv2.compareHist, HISTCMP_CORREL）实现场景变更检测
- 三元触发条件：首帧强制提取 + 最大间隔兜底（60s 防断层）+ 场景变化触发（correlation < 0.90 且 time_since_last ≥ 2s）
- 自适应探测帧率：短视频 5fps / 中等 3fps / 长视频 1fps，显著降低 OpenCV decode 开销
- grab/retrieve 分离优化：仅探测帧调用 retrieve() 获取 NumPy 数组，非探测帧仅 grab() 推进解码器
- 输出支持 inline base64 和 frame_file 引用两种模式，长边超 768px 自动等比缩小

**简历话术方向**：
- "设计场景感知智能抽帧算法，基于灰度直方图相关系数检测画面变化，仅在场景切换时提取关键帧，相比固定间隔抽帧减少 60%+ 冗余帧"
- "实现自适应探测帧率策略（5/3/1 fps 按视频时长自动降级）+ grab/retrieve 分离优化，支撑 30min+ 长视频的高效处理"

**可量化角度**：相比固定间隔的帧数减少比、Vision Token 节省比、抽帧处理耗时、支持的最大视频时长

---

## 亮点 6：Checkpoint 会话持久化与时间旅行追问

**技术要点**：
- 基于 LangGraph Checkpointer 实现工作流状态持久化，支持 InMemory（开发）和 PostgresSaver（生产）双后端
- Checkpoint 工厂模式 + 内存缓存（`key = "{backend}:{url}"`），避免重复创建
- thread_id 贯穿 UI → Service → API → Graph 全链路，同一视频分析会话可断点恢复
- 时间旅行追问：用户指定时间戳 → parse_timestamp_to_seconds → extract_transcript_window（语音时间窗抽取）→ find_nearest_keyframe（最近邻关键帧）→ 证据约束问答
- 降级策略：无 API Key 或 API 异常时返回结构化"证据片段 + 降级原因"

**简历话术方向**：
- "基于 LangGraph Checkpointer 实现会话级状态持久化，支持同一视频分析的断点恢复与历史追问，thread_id 贯穿前端到图引擎全链路"
- "设计时间旅行追问机制，通过 Checkpoint 回溯历史状态 + 时间窗语音抽取 + 最近邻关键帧定位，实现对视频任意时间点的精准追问"

**可量化角度**：会话恢复成功率、追问响应延迟、Checkpoint 后端切换成本、时间点定位精度

---

## 亮点 7：两阶段工作流与 HITL 人类在环审批

**技术要点**：
- 将工作流拆分为两阶段：Phase 1（提取 → 分片 → 聚合 → 人审关口）和 Phase 2（审批后成文 → 质检闭环）
- human_gate_node 作为两阶段分界点，Phase 1 止于 `pending` 状态返回 review_package
- 用户可在前端查看并编辑聚合稿（editable_aggregated_insights）、补充 Human Guidance
- Phase 2 通过 finalize_summary(thread_id) 基于 Checkpoint 恢复状态并注入人工编辑内容
- fusion_drafter_node 将 human_guidance 作为最高优先级证据约束注入 Prompt

**简历话术方向**：
- "设计两阶段工作流架构，在信息密度最高点插入人类审批关口（HITL），用户可编辑聚合稿并补充指导意见，再触发质检闭环成文"
- "Phase 1 与 Phase 2 通过 Checkpoint 持久化衔接，审批断点不丢失上下文，human_guidance 作为最高优先级证据约束注入最终生成"

**可量化角度**：审批通过率、人工编辑率、审批后总结质量提升幅度

---

## 亮点 8：Send API 波次并行调度

**技术要点**：
- 当前主架构固定 `send_api`，通过 `map_dispatch_node` 按波次派发 active chunks
- 使用 `synthesis_barrier_node` 等待波次就绪后再触发融合 worker
- `route_after_wave_synthesis` 控制 continue_wave / wave_done，直至进入聚合
- 通过 reducer 解决并行写冲突，并配合 timeout/retry/degraded 机制保障活性

**简历话术方向**：
- "设计基于 Send API 的波次并行调度架构，通过 active wave 派发与 barrier 汇聚实现可控并发"
- "引入 synthesis_barrier + wave 路由实现 fan-out/fan-in 精确控制，并以容错降级策略避免单分片阻塞全局"

**可量化角度**：波次并发度、长视频稳定性、分片级错误隔离率

---

## 亮点 9：实时状态透传与前端交互体系

**技术要点**：
- LangGraph `stream(stream_mode="updates")` 流式事件按节点粒度实时发射
- api.py 事件循环解析节点更新，构造 `[[PROGRESS]]` JSON payload（含 audio_done/vision_done/synthesis_done/overall_percent）
- status_callback 闭包从 app.py 注入，逐层传递至 Service → API → Graph，无需修改节点签名
- Streamlit 前端四通道进度条（音频/视觉/融合/总体）+ 节点级状态日志
- `st.session_state` 持久化会话状态：current_summary / active_thread_id / pending_review / time_travel_answer
- 人工审批面板：可编辑聚合稿 + Human Guidance 输入 + 审批触发

**简历话术方向**：
- "设计从图引擎到前端的全链路实时状态透传机制，基于 LangGraph 流式事件 + 闭包注入 + Streamlit 进度面板，实现分片级处理过程零黑盒"
- "构建 Streamlit 交互前端，支持两阶段审批流程（分析→审批→终稿）、时间旅行追问、会话绑定，覆盖视频总结全生命周期"

**可量化角度**：前端功能区数、状态更新粒度、进度通道数、用户交互步骤数

---

## 亮点 10：工程化测试体系

**技术要点**：
- 分层测试金字塔：单元测试（~97 个）→ 集成测试（~33 个）→ E2E 烟测，合计 ~130 个测试
- 单元测试覆盖：提取算法（场景抽帧/大文件切分/编解码兼容）、工作流节点（10 个节点各自独立测试）、路由函数、工具函数
- 集成测试覆盖：并行分支合并（Reducer 正确性 12 个场景）、Checkpoint 持久化链路、HITL 完整流、Send API 合成流、时间旅行管线
- Mock 外部依赖（LLM API / Whisper / Tavily），测试不依赖真实 API 调用
- 并发验证聚焦 wave 派发与 barrier 汇聚行为，采用机制断言而非脆弱时间断言
- 回归脚本覆盖 send_api 核心链路（分派/汇聚/容错），确保调度演进不引入回归
- E2E 通过 `RUN_E2E` 环境变量控制，默认关闭保护 CI 成本

**简历话术方向**：
- "建立覆盖 ~130 个用例的三层测试金字塔（Unit → Integration → E2E），所有 LLM/Whisper/搜索调用通过 Mock 隔离，测试零 API 成本"
- "设计并发机制验证策略（wave 派发与 barrier 机制断言），避免基于执行时间的脆弱断言，回归脚本保障 send_api 架构演进安全"

**可量化角度**：测试用例总数、测试层级数、Mock 覆盖率、回归通过率、CI 运行零 API 成本

---

## 亮点 11：FastAPI 微服务架构与领域驱动分层设计

**技术要点**：
- 将 Streamlit 单体拆分为 FastAPI 微服务，按三大领域（基础资产域 / 内容加工域 / 知识检索域）完成数据库模型与服务层的分层重构
- 严格四层边界约束：Routes（协议层）→ Services（业务编排）→ Repositories（数据访问）→ Models（ORM 实体），跨层依赖一律禁止
- Repository 模式 + 依赖注入：所有路由通过 `Depends()` 注入 Repository 实例，服务层 0 感知 HTTP 层
- Controller 字段写权限收敛：用户可写字段（如 `file_name`, `user_guidance`）与系统专属字段（如 `workflow_state`, `oss_key`）在 schema 层强制区分，越权写入触发 422 Validation Error
- PostgreSQL + SQLAlchemy ORM + Alembic 迁移：8 张领域表，多对多关系采用隐式中间表（`Table + secondary`），ON DELETE CASCADE 级联约束
- 软删除生命周期：视频资源 DELETE 接口立即返回 202 Accepted，异步 Celery 任务依次完成 OSS / 向量库 / DB 清理，支持重试与幂等

**简历话术方向**：
- "将 Streamlit 单体重构为 FastAPI 三域微服务架构，严格四层边界隔离（Routes/Services/Repositories/Models），Controller 字段写权限精细化管控，用户接口仅暴露业务意图，状态字段全部由系统流程内部推进"
- "基于 SQLAlchemy + Alembic 完成三大领域 8 张表的领域化重构，多对多关系采用隐式 Join Table 模式，支持软删除生命周期与异步级联清理"

**可量化角度**：领域数量、路由数量、数据库表数、迁移脚本版本数、字段权限收敛覆盖率

---

## 亮点 12：Celery + Redis 异步任务队列与领域事件总线

**技术要点**：
- 上传完成后，`async_finalize_upload` 任务负责：合并分片 → 上传 OSS → 写入 `video_resource.oss_key` → 发布 `VideoUploadedEvent` 到 Redis Streams
- 领域事件总线（`DomainEventBus`）封装 Redis Streams XADD/XREADGROUP/XACK，`domain_event_listener` 作为独立 Worker 消费 `video_uploaded` 事件并路由到 `async_process_video`
- 上传域与内容加工域完全解耦：`upload_finalize_tasks` 不 import 任何 `VideoResourceService`，通过事件契约通信，Consumer Group ACK 保证至少消费一次
- 多阶段异步处理链路：上传 → OSS → 转录（Whisper）→ 关键帧抽取 → LangGraph 两阶段工作流，每阶段均为独立 Celery 任务，支持超时重试
- Celery 任务通过 `acks_late=True` 避免消费失败丢任务；状态推进统一由 `VideoResourceService` 收口，禁止 Task 层直写 Repository

**简历话术方向**：
- "基于 Redis Streams 实现领域事件总线，上传域仅发布 VideoUploadedEvent，内容加工域独立消费，Consumer Group ACK 保证至少消费一次，实现跨域异步解耦"
- "设计多阶段 Celery 任务链（上传→转录→抽帧→总结生成），状态推进统一由 Service 层收口，Task 层只做异步入口和重试封装"

**可量化角度**：异步任务数量、事件类型数、跨域解耦层数、任务重试策略数、消息 ACK 保障级别

---

## 亮点 13：TUS 分片上传协议与大文件断点续传

**技术要点**：
- 完整实现 TUS 1.0.0 协议：`POST`（初始化）/ `HEAD`（进度查询）/ `PATCH`（分片上传）/ `DELETE`（取消）四个端点
- 10 MiB 固定分片大小，`Upload-Offset` 标准头保证客户端可从任意偏移量续传，支持 500 MB+ 视频无障碍上传
- 分片元数据存 Redis（上传会话状态 + 已上传分片列表），分片文件暂存本地，合并完成后统一上传 OSS 并触发领域事件
- 服务端通过 `Tus-Resumable` 版本头做协议版本校验（不匹配返回 412），通过 `Upload-Length` 限制单文件最大 10 GB
- 上传最终化后立即异步发布 `VideoUploadedEvent`，前端通过 WebSocket 监听进度无需轮询

**简历话术方向**：
- "完整实现 TUS 1.0.0 分片上传协议，支持断点续传与 500 MB+ 大文件，分片元数据存 Redis 保障会话可恢复，合并后自动触发内容加工流水线"
- "上传链路与内容加工链路通过 Redis Streams 领域事件异步衔接，前端全程 WebSocket 推送进度，无需主动轮询"

**可量化角度**：支持的最大文件大小、分片大小、上传会话恢复成功率、协议端点数

---

## 亮点 14：WebSocket 实时进度推送与 FCM 移动端通知

**技术要点**：
- WebSocket 端点 `/ws/progress` 通过 JWT query param 鉴权，认证失败立即 close(code=4001)，防止匿名监听
- Redis Pub/Sub 实现跨实例事件分发：`ProgressPublishService` 发布进度事件，`ConnectionManager` 订阅并推送给各自 WebSocket 客户端
- `WSEventEnvelope` 统一消息格式：含 `event_id`, `schema_version`, `sequence`, `stage`, `substage`, `progress`, `payload` 等字段；`sequence` 递增用于客户端重连补偿
- 重连握手：客户端携带 `last_sequence` 重连，服务端返回 `reconnect_ack` 事件，客户端据此丢弃已处理消息
- FCM 推送（Firebase Admin SDK lazy init）：用户后台/关闭状态下通过设备令牌推送，支持多设备 best effort，推送失败仅记录日志不阻塞主流程
- 工作流完成事件在 `payload.result` 中携带 `draft_summary` / `final_summary` 字段，前端可直接渲染无需额外 GET 请求

**简历话术方向**：
- "基于 Redis Pub/Sub 实现 WebSocket 跨实例事件分发，序列号机制支持客户端断线重连补偿，JWT query param 鉴权防止匿名监听"
- "实现 WebSocket + FCM 双通道实时通知，应用前台走 WebSocket 实时推送，后台/关闭状态走 Firebase FCM，complete 事件携带完整结果字段，减少前端额外请求"

**可量化角度**：事件类型数、序列号跨度、多实例支持情况、推送通道数

---

## 亮点 15：Hybrid RAG 多模态检索问答管线

**技术要点**：
- 基于 `modular_rag` 本地 MCP 服务实现 HybridSearch（稠密 + 稀疏双路召回） + Reranker（相关性精排），支持按 `collection` 字段物理隔离不同视频/知识库的向量索引
- `KeyframeLookup.find_nearest` 以检索结果的 `start_s` 时间戳为锚点，在当前视频关键帧列表中找最近邻帧；帧文件按需从 OSS 下载并本地缓存（`temp/frames/rag/`），相同帧名称复用缓存
- `RagStreamLLM.stream_text` / `stream_multimodal` 双模接口：无关键帧时走纯文本 RAG；有关键帧时以 `data:{mime_type};base64,{b64}` 格式将图片编码注入多模态 LLM，token 到达即 yield
- 多模态附件 pipeline：用户 QA 提问时随附图片 → `POST /api/v1/attachments/upload` 上传到 OSS → `oss_key` 随提问发送 → `_download_attachment_frames` 从 OSS 下载并 extend 到 RAG 检索帧 → 一同送入 `stream_multimodal`
- Reranker fallback 策略：精排异常时保留召回原始顺序并记录 `fallback_reason`，QA 不中断

**简历话术方向**：
- "设计 Hybrid RAG 检索管线（双路召回 + 重排序 + 关键帧最近邻匹配），向量索引按视频/知识库 collection 物理隔离，检索结果自动关联时间戳对齐的视频关键帧"
- "实现多模态 QA 附件 pipeline：图片上传 → OSS 持久化 → oss_key 随提问传递 → LLM 上下文注入，真实 LLM token 流 SSE 推送，reranker 降级不中断问答"

**可量化角度**：召回路数、重排序候选数、帧缓存命中率、多模态附件支持数、token 流延迟

---

## 亮点 16：OpenTelemetry 全链路追踪

**技术要点**：
- 基于 OpenTelemetry SDK 构建链路追踪，支持 Jaeger Thrift 与 OTLP gRPC 双导出器，采样率可配置
- W3C `traceparent/tracestate/baggage` 头传播：HTTP 请求进入时提取 trace_id，写入 `request.state.trace_id`，注入 Celery 任务参数，实现 HTTP → Celery → LangGraph → LLM 全链路 Span 连通
- Span 命名遵循 `{domain}.{stage}.{action}` 三段式规范（如 `http.request.handle`），LLM Span 严格不记录 prompt/transcript/token 等敏感字段，通过 OWASP Top 10 合规
- 中间件层拦截：`otel_middleware` 对所有 HTTP 请求自动创建根 Span；若上游已有 traceparent 则接续，否则生成新 trace_id
- `backend/observability/llm_tracing.py` 对 LLM 调用统一打 Span，记录 model、duration、token 数量等非敏感指标

**简历话术方向**：
- "基于 OpenTelemetry 实现 HTTP → Celery → LLM 全链路追踪，W3C traceparent 跨进程传播，Span 命名三段式规范化，敏感字段（prompt/transcript）不上报，对齐安全合规要求"
- "支持 Jaeger Thrift / OTLP gRPC 双导出器、采样率动态配置，中间件自动拦截根 Span 创建，LLM 调用统一打点，可观测性贯穿整条请求链路"

**可量化角度**：追踪跨越的进程/服务数、Span 类型数、敏感字段屏蔽项数、导出器种类

---

## 亮点 17：移动端适配与统一错误处理体系

**技术要点**：
- `MobileOptimizationMiddleware`：GET 成功响应自动注入 `Cache-Control: private, max-age=60` + SHA-256 前 16 位 ETag，客户端 If-None-Match 命中返回 304，有效减少重复数据传输
- `GZipMiddleware`：响应体 > 1 KB 自动压缩，低带宽移动网络下显著降低流量
- 分页协议标准化：`page / page_size（默认 20，最大 100）/ cursor / has_next` 统一封装，`next_cursor` 支持基于游标的无状态翻页
- 统一错误信封 `{status, data, error: {code, message, details, is_retryable, retry_after}, meta}`：`AppError` 基类对应业务异常，`RequestValidationError` / `HTTPException` / 兜底 500 全部拦截，不暴露堆栈信息
- 结构化 JSON 访问日志：每条请求记录 `request_id`, `user_id`, `trace_id`, `path`, `method`, `status_code`, `duration_ms`，通过 `request_context` 中间件注入 request_id

**简历话术方向**：
- "实现 ETag 条件请求 + GZip 双重优化，移动端 304 命中可减少约 50% 冗余数据传输；统一分页协议支持游标翻页，适配低端 Android 设备弱网场景"
- "设计统一错误处理体系：AppError 基类 + 三类异常全局拦截，错误信封含 is_retryable/retry_after 字段辅助客户端重试决策，结构化访问日志含全链路 trace_id"

**可量化角度**：ETag 缓存命中率、GZip 压缩比、304 节省带宽比例、错误码覆盖类型数
