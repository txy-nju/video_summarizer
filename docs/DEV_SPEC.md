# Video Summarizer 技术文档

> 多模态视频智能总结系统 · 工程化技术规格说明书

---

## 目录

1. [项目概述](#1-项目概述)
2. [核心特点](#2-核心特点)
3. [技术选型](#3-技术选型)
4. [测试方案](#4-测试方案)
5. [系统架构与模块设计](#5-系统架构与模块设计)

---

## 1. 项目概述

### 1.1 设计理念

Video Summarizer 的核心理念是**让 AI 像人类专家一样看完整个视频后再做总结**——而非对字幕做粗暴截断，也非只看文本不看画面。

当前主流视频总结工具（Glarity、NoteGPT、Eightify 等）存在三大普遍痛点：

| 痛点 | 现象 | 根因 |
|------|------|------|
| **上下文丧失** | 长视频总结出现凭空捏造内容 | 直接截断或强压缩，破坏叙事完整性 |
| **视觉盲视** | 无字幕视频（代码演示、UI 操作、白板讲解）无法处理 | 仅依赖文本通道，忽略视觉信息 |
| **交互缺失** | 一次生成后不可追问、不可回溯 | 无状态持久化，无法定位历史上下文 |

本项目针对以上痛点，构建了一套**多模态 + 多智能体 + 状态持久化**的工程化解决方案：

- 通过 **Map-Reduce 分片并行** 保持长视频的时空上下文完整性
- 通过 **音视双通道多智能体协作** 实现跨模态联合理解
- 通过 **Checkpoint + Time Travel** 支持交互式深度钻取
- 通过 **Self-RAG 双重质检闭环** 根治表面流畅但事实不准的幻觉问题
- 通过 **人类在环（HITL）审批** 在 AI 自动化与人工把控之间取得平衡

### 1.2 项目定位

这不是一个单次输出摘要的脚本，而是一套包含**提取层、工作流层、会话持久化层、前端交互层和测试闭环**的完整工程化实现，具备以下工程特质：

- **可扩展**：提取策略（URL / Upload）可独立扩展，分片并发采用 send_api 图级 fan-out/fan-in
- **可回溯**：基于 Checkpoint 的状态持久化机制支持断点恢复与历史追问
- **可审计**：两阶段工作流 + 人类审批关口 + 结构化指标日志
- **可测试**：~130 个测试覆盖节点、路由、并发合并、集成链路和 E2E 烟测

---

## 2. 核心特点

### 2.1 多模态联合理解

同时利用 **Whisper 语音转录文本**与 **视觉模型分析关键帧**，对视频内容进行图文融合分析。两个分析通道并行执行，各自独立生成洞察后在融合阶段进行对齐，避免信息盲区。

### 2.2 Map-Reduce 分片并行架构

面对长视频，系统将 transcript 和 keyframes 按时间轴切分为多个 chunk（默认 120 秒/片），对每个 chunk 并行执行音频分析、视觉分析和分片融合，最后按时间顺序聚合为完整的证据底稿。这一设计同时解决了上下文窗口限制和处理效率问题。

### 2.3 两阶段工作流与 HITL 审批

工作流拆分为两个阶段：

- **阶段一（analyze）**：提取 → 分片分析 → 聚合 → 人工审批关口
- **阶段二（finalize）**：审批通过 → 全篇成文 → 双重质检闭环

两阶段之间通过 Checkpoint 持久化衔接。用户可在审批关口查看并编辑聚合稿、补充指导意见，再触发最终生成。

### 2.4 Self-RAG 双重质检闭环

最终总结生成后依次经过两道质检：

1. **幻觉核查（Hallucination Grader）**：以 JSON Mode + temperature=0 验证总结内容是否脱离原始证据
2. **有用性核查（Usefulness Grader）**：验证总结是否命中用户指定的侧重需求

任一检查不通过则携带修正指令回流至 Fusion Drafter 重写，最多重写 2 轮后熔断放行，防止死循环。

### 2.5 主动求知（ReAct + Tavily Search）

当音频或视觉分析节点遇到生僻术语、热梗、未知 UI 或缩写时，可在分析过程中主动调用 Tavily 搜索引擎补充背景知识（最多 2-3 次 tool call），将检索结果融入分析输出，增强总结的专业深度。

### 2.6 场景感知智能抽帧

替代传统固定间隔抽帧，采用**基于灰度直方图相关系数的场景变更检测**（阈值 0.90），仅在画面发生实质变化时提取关键帧，辅以最小间隔（2s）和最大间隔（60s）双重兜底。长视频还会自适应降低探测帧率（5→3→1 fps），显著减少视觉 Token 消耗。

### 2.7 会话持久化与时间旅行追问

通过 thread_id + Checkpoint 机制保存每次分析的完整状态。用户在总结完成后可指定任意时间戳继续提问，系统自动回溯该时间窗附近的语音片段和最近邻关键帧，以证据约束方式生成回答。

### 2.8 Send API 并发架构

系统并发路径已收敛为 `send_api`：通过 LangGraph 原生 fan-out/fan-in 对分片任务进行并行调度，并在 reducer 阶段统一做深度合并。该模式提供分片级进度追踪与更清晰的任务隔离语义。

### 2.9 实时状态透传

LangGraph 流式事件通过 `status_callback` 闭包实时透传至 Streamlit 前端，包括分片级进度条（音频/视觉/融合各通道独立进度）和节点级状态日志，实现"零黑盒"处理体验。

---

## 3. 技术选型

### 3.1 技术栈总览

| 层级 | 技术 | 选型理由 |
|------|------|----------|
| **前端** | Streamlit | 快速原型 + 丰富的交互组件（`st.status`、`st.progress`、`st.session_state`），适合数据密集型 AI 应用 |
| **工作流引擎** | LangGraph (StateGraph) | 原生支持有状态图、条件路由、并行分支、Checkpoint 持久化、Send API fan-out |
| **大语言模型** | OpenAI 兼容接口 (GPT-4o) | 多模态能力 + 广泛的中转服务兼容性（通过 `OPENAI_BASE_URL` 适配） |
| **语音转录** | OpenAI Whisper (`whisper-1`) | `verbose_json` 格式保留时间戳分段，支撑分片规划和时间旅行 |
| **视频下载** | yt-dlp | YouTube 及多平台支持，可携带 cookies.txt 绕过认证限制 |
| **音频提取** | moviepy | Python 原生 FFmpeg 封装，兼容性广泛 |
| **关键帧提取** | OpenCV (cv2) | 灰度直方图计算 + grab/retrieve 分离优化（减少内存拷贝） |
| **网络搜索** | Tavily API (urllib 原生) | 轻量集成无额外依赖；无 API Key 时优雅降级 |
| **重试机制** | tenacity | 声明式重试策略，用于 Whisper API 和网络请求 |
| **环境配置** | python-dotenv + config/settings.py | 集中管理，支持 `.env` 文件和环境变量双通道 |

### 3.2 工作流引擎设计

#### 为什么选择 LangGraph

LangGraph 提供的 StateGraph 能力契合本项目的核心需求：

- **有状态图执行**：VideoSummaryState 贯穿整个工作流，节点间通过状态字段传递数据
- **条件路由（Conditional Edges）**：幻觉/有用性质检的闭环回流通过条件边实现
- **并行分支**：音频分析与视觉分析天然可并行，LangGraph 原生支持 fan-out/fan-in
- **自定义 Reducer**：`_merge_chunk_results` 实现并行分支的深度合并，避免数据覆盖
- **Checkpoint 持久化**：内存/Postgres 双后端，支持断点恢复和时间旅行
- **Send API**：图级 fan-out 支持动态数量的并行任务分发

#### Send API 并发实现

```
send_api 模式：
   map_dispatch_node → route_audio_send_tasks() → Send(audio_worker × N)
                              → route_vision_send_tasks() → Send(vision_worker × N)
  → synthesis_barrier_node  # 等待所有音视频完成
  → route_synthesis_send_tasks() → Send(synthesizer_worker × N)
  → chunk_synthesizer_node
```

### 3.3 提取层设计

#### 模板方法模式

提取层使用经典的**模板方法模式**，`VideoSource` 基类定义固定的处理流程，子类仅实现差异化的视频获取步骤：

```python
class VideoSource(ABC):
    def process(status_callback) → Tuple[str, List[Dict]]:
        video_path = self.acquire_video(status_callback)  # 子类实现
        audio_path = self.extractor.extract_audio(video_path)
        frames = self.extractor.extract_frames(video_path)
        transcript = self.transcriber.transcribe(audio_path)
        return transcript, frames
```

- `UrlVideoSource`：通过 yt-dlp 下载视频（支持 cookies.txt 认证）
- `LocalFileVideoSource`：将用户上传的文件流落盘为本地文件

#### 场景感知抽帧算法

```
输入：视频文件路径
参数：interval=2s, max_interval=60s, threshold=0.90

1. 自适应探测帧率选择
   - 视频时长 < 10min  → 5 fps
   - 视频时长 10-30min → 3 fps
   - 视频时长 ≥ 30min  → 1 fps

2. 双层采样优化（grab/retrieve 分离）
   - 每帧调用 grab() 推进解码器
   - 仅在探测帧 (frame_count % probe_stride == 0) 调用 retrieve()

3. 帧提取三元触发条件
   - 条件 A：首帧强制提取
   - 条件 B：time_since_last ≥ max_interval（防长时断层）
   - 条件 C：time_since_last ≥ interval AND cv2.compareHist(correlation) < threshold（场景变化）

4. 输出：[{time: "MM:SS", image: "base64"} | {time: "MM:SS", frame_file: "filename"}]
```

#### 大文件转录策略

Whisper API 有 25MB 上传限制，系统实现递归二分切分：

```
1. 预估分段数：n = ceil(file_size / 18MB)    # VBR 安全余量
2. 队列初始化：按均分时长生成初始段
3. 循环处理：
   - 若段大小 > 24MB 且时长 > 2s 且递归深度 < 6：二分再入队
   - 否则：调用 Whisper API 转录当前段
4. 合并：按时间戳递增拼接 verbose_json segments，segment_id 重编号
```

### 3.4 状态管理设计

#### VideoSummaryState 核心字段

```python
class VideoSummaryState(TypedDict):
    # ── 输入核心 ──
    transcript: str                    # Whisper verbose_json
    keyframes: List[Dict]              # [{time, image|frame_file}]
    keyframes_base_path: str           # 关键帧文件引用基础路径
   user_prompt: str                   # 用户侧重需求

    # ── 分片规划 ──
    video_duration_seconds: int
    chunk_plan: List[Dict]             # [{chunk_id, start_sec, end_sec, keyframe_indexes, transcript_segment_indexes}]

    # ── 分片结果（带自定义 Reducer）──
    chunk_results: Annotated[List[Dict], _merge_chunk_results]

    # ── Send API Worker 上下文 ──
    current_chunk: Optional[Dict]
    current_synthesis_chunk: Optional[Dict]
    current_synthesis_base_item: Optional[Dict]

    # ── 聚合与输出 ──
    aggregated_chunk_insights: str     # 按时间顺序拼接的完整证据底稿
    draft_summary: str                 # 最终 Markdown 总结

    # ── 质检闭环 ──
    hallucination_score: str           # "yes" | "no"
    usefulness_score: str              # "yes" | "no"
    feedback_instructions: str         # 修正指令（回流时携带）
    revision_count: int                # 重写计数（熔断阈值 = 2）

    # ── HITL 人类审批 ──
    human_gate_status: Optional[str]   # "pending" | "approved" | "redirected"
    human_edited_aggregated_insights: str
    human_guidance: str

    # ── 可观测性 ──
    chunk_retry_count: Dict
    reduce_debug_info: Dict
    token_usage: Dict
    latency_ms: Dict
```

#### 并行分支合并策略（Reducer）

`_merge_chunk_results` 实现了深度合并语义：

1. 按 `chunk_id` 索引，保持 base 列表的原始顺序
2. 重叠 chunk 的字段合并策略：base 值优先，update 补充缺失字段
3. `latency_ms` 等嵌套 dict 类型递归合并（保留所有时间维度）
4. update 中的新增 chunk 追加到末尾

### 3.5 会话与持久化设计

#### Checkpoint 工厂

```python
create_checkpointer(backend, postgres_url) → InMemorySaver | PostgresSaver
```

- 使用工厂模式 + 内存缓存（`key = "{backend}:{url}"`），避免重复创建
- 默认 `memory` 后端（开发环境），可通过配置切换到 `postgres`（生产环境）

#### Time Travel 机制

```
输入: thread_id + timestamp + question + window_seconds

1. Checkpointer 读取 channel_values（完整历史状态）
2. parse_timestamp_to_seconds("MM:SS" | "HH:MM:SS") → 秒数
3. extract_transcript_window(transcript, target_sec, window) → 时间窗内语音文本
4. find_nearest_keyframe(keyframes, target_sec) → 最近邻关键帧
5. 构建证据约束提示 → LLM 生成回答
6. 降级策略：无 API Key 或 API 异常时返回结构化"证据片段 + 降级原因"
```

### 3.6 前端交互设计

Streamlit 前端提供以下核心交互能力：

| 功能区 | 说明 |
|--------|------|
| **Settings 侧边栏** | API Key / Base URL / 视频来源选择 / 用户侧重需求 / 会话绑定 |
| **视频预览区** | YouTube URL 内嵌播放 / 上传视频本地播放 |
| **实时状态面板** | `st.status` 容器 + 分片级进度条（音频/视觉/融合/总体四通道） |
| **人工审批区** | 可编辑聚合稿 + Human Guidance 输入 + 审批按钮触发第二阶段 |
| **总结展示区** | Markdown 渲染最终总结 + 历史缓存 |
| **Time Travel 区** | 时间戳输入 + 证据窗口滑块 + 追问输入 + 回溯结果展示 |

### 3.7 配置与开关体系

所有配置通过 `config/settings.py` 集中管理，支持 `.env` 文件和环境变量双通道输入：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `OPENAI_API_KEY` | - | OpenAI / 兼容 API Key |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 基础地址（支持中转） |
| `OPENAI_MODEL_NAME` | `gpt-4o` | 默认语言模型 |
| `OPENAI_VISION_MODEL_NAME` | 回退为 `OPENAI_MODEL_NAME` | 视觉分析专用模型 |
| `TRANSCRIBER_MODEL` | `whisper-1` | 语音转文本模型 |
| `TAVILY_API_KEY` | 可选 | 搜索工具 API Key |
| `MAP_CHUNK_SECONDS` | `120` | 分片时长（秒） |
| `MAP_CHUNK_OVERLAP_SECONDS` | `10` | 分片重叠时长 |
| `MAP_MAX_PARALLELISM` | `4` | 分片并行度配置（预留） |
| `CHUNK_MAX_TOOL_CALLS` | `2` | 每个分片最大搜索调用次数 |
| `AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS` | `24000` | 聚合稿截断上限 |
| `CHECKPOINT_BACKEND` | `memory` | Checkpoint 后端：`memory` / `postgres` |
| `CHECKPOINT_DB_URL` | - | Postgres 连接地址 |
| `ENABLE_KEYFRAME_FILE_REFERENCE` | `false` | 关键帧文件引用化开关 |
| `ENABLE_METRICS_LOGGING` | `true` | 运行指标采样开关 |
| `METRICS_SAMPLE_RATE` | `1.0` | 指标采样率 (0.0-1.0) |

---

## 4. 测试方案

### 4.1 测试理念

本项目的测试遵循以下核心原则：

- **Mock 外部依赖，验证内部逻辑**：所有 LLM API 调用、Whisper 转录、Tavily 搜索均通过 Mock 隔离，确保测试不依赖真实 API 调用
- **状态转移为核心断言目标**：每个节点测试重点验证 VideoSummaryState 的字段变更是否符合预期
- **机制验证优于时间断言**：并发测试通过 Send 路由与 reducer 断言验证并行语义，而非脆弱的执行时间断言
- **分层覆盖，逐级递进**：工具函数 → 单个节点 → 节点组合 → 完整图执行 → 端到端烟测

### 4.2 分层策略

#### 第一层：单元测试（~73 个 · `tests/core/generation/`）

覆盖工作流层的每个独立节点和工具函数：

| 测试文件 | 测试数 | 测试目标 | 关键场景 |
|----------|--------|----------|----------|
| `test_chunk_planner.py` | 8 | 分片规划节点 | 正常规划 / 空输入 / 畸形 JSON / 时长回退推断 |
| `test_chunk_audio_analyzer.py` | - | 已移除旧 analyzer 测试 | 音频路径改为 worker + reducer 汇聚 |
| `test_chunk_vision_analyzer.py` | - | 已移除旧 analyzer 测试 | 视觉路径改为 worker + reducer 汇聚 |
| `test_chunk_synthesizer.py` | 6 | 分片融合节点 | 音视融合 / 并行性能 |
| `test_chunk_aggregator.py` | 3 | 聚合节点 | 顺序拼接 / 空 chunks / 超长截断 |
| `test_fusion_drafter.py` | 3 | 成文节点 | Markdown 生成 / feedback 回流 |
| `test_hallucination_grader.py` | 7 | 幻觉核查节点 | JSON Mode 解析 / 熔断逻辑 / 修正指令生成 |
| `test_usefulness_grader.py` | 7 | 有用性核查节点 | prompt 对齐 / 无 prompt 放行 / human_guidance 融入 |
| `test_graph_routing.py` | 4 | 条件路由函数 | 幻觉/有用性决策树 |
| `test_map_dispatcher.py` | 12 | 分发节点 | Send payload 构造 / 合成屏障 / 路由门控 |
| `test_human_gate_routing.py` | 3 | HITL 路由 | pending / approved 状态转移 |
| `test_frame_utils.py` | 2 | 帧工具函数 | inline_image / frame_file 优先级 |
| `test_time_travel.py` | 5 | 时间旅行工具 | 时间戳解析 / 最近邻匹配 / 窗口抽取 |
| `test_concurrency_mode_config.py` | 1 | 并发配置 | 固定 send_api 配置校验 |
| `tools/test_search_tools.py` | - | Tavily 搜索工具 | urllib 实现 / 无 Key 降级 |

#### 第二层：单元测试（~24 个 · `tests/core/extraction/`）

覆盖提取基础设施的每个组件：

| 测试文件 | 测试数 | 测试目标 | 关键场景 |
|----------|--------|----------|----------|
| `infrastructure/test_extractor.py` | 12 | MediaExtractor | 音频提取（正常/无音轨/文件缺失） / 场景感知抽帧（直方图阈值/FPS 异常/超长视频自动降频/首帧强制/流中断恢复） |
| `infrastructure/test_transcriber.py` | 10 | AudioTranscriber | moviepy 版本兼容 / 切分策略（24MB 硬限/18MB 目标/递归二分） / verbose_json 时间戳回拼 / 单路径 vs 合并路径等价性 |
| `infrastructure/video/test_downloader.py` | 1 | VideoDownloader | yt-dlp + cookies.txt / 格式选择 |
| `infrastructure/video/test_local_video_handler.py` | 2 | LocalVideoHandler | 文件流落盘 / 防御性 mkdir |
| `sources/test_local_source_integration.py` | 1 | LocalFileVideoSource | 完整流程：acquire → extract_audio → extract_frames → transcribe |

#### 第三层：集成测试（~33 个 · `tests/integration/`）

验证多模块协作和完整链路：

| 测试文件 | 测试数 | 集成范围 | 关键验证 |
|----------|--------|----------|----------|
| `test_graph_level_parallelism.py` | 12 | 并行分支合并 | Reducer 正确性（空/不相交/重叠/latency 递归/顺序保持） + 实际并行执行验证 |
| `test_checkpoint_restore_flow.py` | 2 | Checkpoint 持久化 | 状态保存 → 同 thread_id 恢复 → 版本覆盖 |
| `test_time_travel_pipeline.py` | 5 | 时间旅行问答 | 成功场景 / thread 缺失 / API 降级 / 超时 / 参数验证 |
| `test_workflow_service_session.py` | 2 | 服务层会话 | thread_id 复用逻辑 |
| `test_api_status_messages.py` | 6 | 状态回调流 | planner / analyzer / dispatcher / synthesizer / human_gate 消息格式 |
| `test_synthesis_send_api_flow.py` | 2 | Send API 合成流 | 并行 worker 执行验证 |
| `test_human_review_finalize_flow.py` | 2 | HITL 完整流 | pending 审批包 → finalize 回流 |
| `test_metrics_logging.py` | 1 | 指标发射 | 事件日志格式验证 |
| `test_e2e_pipeline.py` | 1 | 全链路烟测 | RUN_E2E=true 时调用真实 API 执行完整流程 |

#### 第四层：回归与性能基线（`scripts/`）

| 脚本 | 用途 |
|------|------|
| `ab_full_regression_3rounds.py` | A/B 模式三轮回归对比，统计 pytest 通过率 |
| `ab_mode_baseline_sample.py` | 单轮 send_api 性能采样基线 |
| `check_openai_api.py` | API 连接可用性检测 |

### 4.3 质量评估

#### 测试覆盖总览

| 测试层级 | 测试数量 | 覆盖重点 |
|----------|----------|----------|
| 单元测试（generation） | ~73 | 节点逻辑、状态转移、路由决策 |
| 单元测试（extraction） | ~24 | 提取算法、编解码兼容性、容错 |
| 集成测试 | ~33 | 多模块协作、持久化链路、并发合并 |
| **合计** | **~130** | - |

#### 关键测试模式

| 模式 | 应用场景 | 说明 |
|------|----------|------|
| Mock LLM API | 所有节点测试 | 避免真实 API 调用的成本和不确定性 |
| 状态字段断言 | 所有节点测试 | 精确验证 VideoSummaryState 字段变更 |
| 并发机制验证 | 并行测试 | Send 路由分发 + reducer 合并断言，非时间断言 |
| Checkpoint 往返 | 持久化测试 | 写入 → 读取 → 验证状态一致性 |
| 降级路径覆盖 | 搜索/转录/API 测试 | 无 Key / 网络异常时的优雅降级 |
| 参数化多场景 | 规划/分析/路由测试 | 正常 / 边界 / 异常输入的参数化覆盖 |

#### E2E 保护策略

端到端测试通过环境变量 `RUN_E2E` 控制，默认关闭（`false`），避免 CI 环境中产生真实 API 调用费用。开启时执行完整的视频下载 → 提取 → 分析 → 质检链路。

---

## 5. 系统架构与模块设计

### 5.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Frontend (Streamlit · app.py)                       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ Settings     │  │ Video Preview│  │ Human Review │  │ Time Travel    │  │
│  │ Sidebar      │  │              │  │ Panel        │  │ Q&A            │  │
│  └──────┬───────┘  └──────────────┘  └──────┬───────┘  └───────┬────────┘  │
│         │           status_callback          │                  │           │
└─────────┼────────────────────────────────────┼──────────────────┼───────────┘
          │                                    │                  │
          ▼                                    ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Service Layer (services/workflow_service.py)              │
│                         VideoSummaryService                                 │
│  ┌──────────────────┐  ┌───────────────────┐  ┌─────────────────────────┐  │
│  │ analyze_url_video │  │ finalize_summary  │  │ ask_at_timestamp        │  │
│  │ analyze_uploaded  │  │                   │  │                         │  │
│  └────────┬─────────┘  └────────┬──────────┘  └────────┬────────────────┘  │
│           │ env inject           │ checkpoint           │ checkpoint        │
└───────────┼──────────────────────┼──────────────────────┼───────────────────┘
            │                      │                      │
            ▼                      ▼                      ▼
┌───────────────────────┐  ┌──────────────────────────────────────────────────┐
│   Extraction Layer    │  │              Workflow Layer (core/workflow/)      │
│  (core/extraction/)   │  │                                                  │
│                       │  │  ┌──────────────┐  ┌────────────────────────┐   │
│  VideoSource.process()│  │  │ analyze_video│  │ finalize_summary       │   │
│   ├── acquire_video   │──▶  │ (Phase 1)    │  │ (Phase 2)              │   │
│   ├── extract_audio   │  │  └──────┬───────┘  └────────┬───────────────┘   │
│   ├── extract_frames  │  │         │                    │                   │
│   └── transcribe      │  │         ▼                    ▼                   │
│                       │  │  ┌──────────────────────────────────────────┐   │
│  ┌─ UrlVideoSource    │  │  │    LangGraph StateGraph (graph.py)       │   │
│  └─ LocalFileVideoSrc │  │  │    VideoSummaryState (state.py)          │   │
│                       │  │  │    Checkpoint (checkpoint_factory.py)     │   │
│  ┌─ MediaExtractor    │  │  │    Session (session.py)                  │   │
│  ├─ AudioTranscriber  │  │  │    Time Travel (time_travel.py)          │   │
│  ├─ VideoDownloader   │  │  └──────────────────────────────────────────┘   │
│  └─ LocalVideoHandler │  │                                                  │
└───────────────────────┘  └──────────────────────────────────────────────────┘
                                        │
                                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Utility & Config Layer                                   │
│  ┌─────────────────────┐  ┌──────────────┐  ┌───────────────────────────┐  │
│  │ config/settings.py  │  │ utils/       │  │ External APIs             │  │
│  │ (集中配置管理)       │  │ file_utils   │  │ OpenAI / Whisper / Tavily │  │
│  │                     │  │ logger       │  │                           │  │
│  └─────────────────────┘  └──────────────┘  └───────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 目录结构

```
video_summarizer/
├── app.py                          # Streamlit 前端入口
├── cookies.txt                     # yt-dlp 认证 cookies
├── requirements.txt                # Python 依赖
├── .env                            # 环境变量（不入库）
│
├── config/
│   └── settings.py                 # 集中配置管理（所有开关和常量）
│
├── core/
│   ├── extraction/                 # 提取层
│   │   ├── base.py                 # VideoSource 抽象基类（模板方法）
│   │   ├── sources/
│   │   │   ├── url_source.py       # UrlVideoSource（yt-dlp 下载）
│   │   │   └── local_source.py     # LocalFileVideoSource（上传保存）
│   │   └── infrastructure/
│   │       ├── extractor.py        # MediaExtractor（音频提取 + 场景感知抽帧）
│   │       ├── transcriber.py      # AudioTranscriber（Whisper + 大文件切分）
│   │       └── video/
│   │           ├── downloader.py   # VideoDownloader（yt-dlp 封装）
│   │           └── local_video_handler.py  # LocalVideoHandler（文件落盘）
│   │
│   └── workflow/                   # 工作流层
│       ├── api.py                  # 对外 API（analyze_video / finalize_summary / answer_question_at_timestamp）
│       ├── session.py              # thread_id 管理
│       ├── checkpoint_factory.py   # Checkpoint 工厂（memory / postgres）
│       ├── time_travel.py          # 时间旅行工具函数
│       └── video_summary/
│           ├── graph.py            # LangGraph 图构建（双图 + send_api 并发）
│           ├── state.py            # VideoSummaryState 定义 + Reducer
│           ├── nodes/
│           │   ├── chunk_planner.py          # 分片规划
│           │   ├── map_dispatcher.py         # 分发与屏障
│           │   ├── chunk_audio_analyzer.py   # 音频分析 worker
│           │   ├── chunk_vision_analyzer.py  # 视觉分析 worker
│           │   ├── chunk_synthesizer.py      # 分片融合
│           │   ├── chunk_aggregator.py       # 证据聚合
│           │   ├── human_gate.py             # 人类审批关口
│           │   ├── fusion_drafter.py         # 全篇成文
│           │   ├── hallucination_grader.py   # 幻觉核查
│           │   └── usefulness_grader.py      # 有用性核查
│           ├── edges/
│           │   └── router.py                 # 条件路由函数
│           ├── tools/
│           │   └── search_tools.py           # Tavily 搜索工具
│           └── utils/
│               └── frame_utils.py            # 关键帧图像解析工具
│
├── services/
│   └── workflow_service.py         # 服务编排层（VideoSummaryService）
│
├── utils/
│   ├── file_utils.py               # 临时目录清理
│   └── logger.py                   # 结构化日志
│
├── tests/
│   ├── core/
│   │   ├── extraction/             # 提取层单元测试
│   │   │   ├── infrastructure/     # MediaExtractor / Transcriber / Downloader
│   │   │   └── sources/            # VideoSource 集成测试
│   │   └── generation/             # 工作流节点单元测试
│   │       └── tools/              # 搜索工具测试
│   ├── integration/                # 集成测试（并行/持久化/HITL/E2E）
│   └── data/
│       └── video_sources.json      # 测试视频源数据
│
├── scripts/                        # 回归与基线脚本
├── temp/                           # 临时文件（自动清理）
├── test_output/                    # 测试产出物
└── docs/                            # 项目文档（设计分析、评估报告、开发规范等）
    ├── analysis/                   # 设计分析文档
    ├── evaluation/reports/         # 评估报告
    ├── DEV_SPEC.md                 # 开发规范文档
    ├── OBSERVABILITY_RUNBOOK_CN.md # 可观测性操作手册
    └── FRONTEND_BACKEND_API_INTERFACE_CN.md # 前后端接口规范
```

### 5.3 模块职责

#### 入口层：`app.py`

Streamlit 页面，职责边界严格限定为 **UI 交互与状态展示**：

- 收集用户输入（API Key / Base URL / 视频来源 / 用户侧重需求 / thread_id）
- 通过 `status_callback` 闭包将工作流进度实时透传至 `st.status` 容器
- 管理 `st.session_state` 持久化会话状态（summary / thread_id / pending_review）
- 人工审批面板：展示可编辑聚合稿 + Human Guidance 输入
- Time Travel Q&A 区：时间戳 / 证据窗口 / 追问问题

#### 服务层：`services/workflow_service.py`

`VideoSummaryService` 作为前端与核心逻辑之间的桥梁：

- 将前端传入的 API Key / Base URL 注入 `os.environ`（解耦 LangGraph 节点签名）
- 统一暴露三个入口：URL 分析 / 上传分析 / 时间旅行追问 / 审批终稿
- 处理完成后调用 `clear_temp_folder()` 清理临时文件
- 管理 `last_thread_id` 供前端回填

#### 提取层：`core/extraction/`

| 组件 | 职责 |
|------|------|
| `VideoSource`（base.py） | 抽象基类，定义 `process()` 模板方法：acquire → extract_audio → extract_frames → transcribe |
| `UrlVideoSource` | 通过 VideoDownloader（yt-dlp）下载视频 |
| `LocalFileVideoSource` | 通过 LocalVideoHandler 保存上传文件 |
| `MediaExtractor` | 音频提取（moviepy）+ 场景感知关键帧提取（OpenCV 灰度直方图） |
| `AudioTranscriber` | Whisper API 转录，>24MB 自动递归二分切段 + verbose_json 时间戳回拼 |
| `VideoDownloader` | yt-dlp 封装，支持 cookies.txt 认证 |
| `LocalVideoHandler` | 文件流落盘，防御性目录创建 |

#### 工作流层：`core/workflow/`

| 组件 | 职责 |
|------|------|
| `api.py` | 对外 API：`analyze_video()` / `finalize_summary()` / `answer_question_at_timestamp()` |
| `graph.py` | 构建两个 LangGraph 图：分析图（Phase 1）+ 终稿图（Phase 2） |
| `state.py` | VideoSummaryState 类型定义 + `_merge_chunk_results` Reducer |
| `session.py` | thread_id 标准化（空值时生成 UUID） |
| `checkpoint_factory.py` | Checkpointer 工厂（memory / postgres） |
| `time_travel.py` | 时间戳解析、最近邻关键帧、语音窗口抽取 |

#### 节点层：`core/workflow/video_summary/nodes/`

| 节点 | 阶段 | 职责 |
|------|------|------|
| `chunk_planner_node` | Phase 1 | 根据 transcript + keyframes 时间戳生成分片计划（两指针扫描 + 堆优化） |
| `map_dispatch_node` | Phase 1 | 分发 send_api 任务，初始化重试计数和调试字段 |
| `chunk_audio_worker_node` | Phase 1 | 处理单分片文本证据 → LLM 摘要 → 可选 Tavily 搜索增强 |
| `chunk_vision_worker_node` | Phase 1 | 处理单分片关键帧 → 视觉模型分析 → 可选 Tavily 搜索增强 |
| `chunk_synthesizer_node` | Phase 1 | 融合每个分片的音频洞察与视觉洞察为 chunk_summary |
| `chunk_aggregator_node` | Phase 1 | 按 chunk_plan 顺序拼接所有分片证据，生成聚合底稿（含截断保护） |
| `human_gate_node` | Phase 1→2 | 人类审批关口，Phase 1 止于 pending，Phase 2 由审批触发 |
| `fusion_drafter_node` | Phase 2 | 基于聚合稿 + 用户需求 + feedback + human_guidance 生成 Markdown 总结 |
| `hallucination_grader_node` | Phase 2 | JSON Mode 事实核查，不通过则生成修正指令并回流（熔断阈值=2） |
| `usefulness_grader_node` | Phase 2 | JSON Mode 需求对齐检查，不通过则回流（无 user_prompt 时直接放行） |

### 5.4 数据流动

#### 主链路（视频总结完整流程）

```
1. 用户输入
   │  api_key, base_url, video_source, user_prompt, thread_id
   ▼
2. VideoSummaryService 选择 Source 实现
   │  UrlVideoSource | LocalFileVideoSource
   ▼
3. VideoSource.process() 产出原始数据
   │  → transcript (Whisper verbose_json)
   │  → keyframes [{time, image|frame_file}]
   ▼
4. analyze_video() 组装初始状态并启动 Phase 1 LangGraph
   │
   ├─ chunk_planner_node
   │    → chunk_plan: [{chunk_id, start_sec, end_sec, keyframe_indexes, transcript_segment_indexes}]
   │    → video_duration_seconds
   │
   ├─ map_dispatch_node（分发策略标记 + 重试初始化）
   │
   ├─ [并行] chunk_audio_worker_node ──→ chunk_results[].audio_insights
   ├─ [并行] chunk_vision_worker_node ──→ chunk_results[].vision_insights
   │    └── _merge_chunk_results (Reducer) 自动合并并行分支
   │
   ├─ chunk_synthesizer_node ──→ chunk_results[].chunk_summary
   │
   ├─ chunk_aggregator_node
   │    → aggregated_chunk_insights (Markdown 格式，按时间顺序)
   │
   └─ human_gate_node
        → human_gate_status = "pending"
        → 返回 review_package（待审批包）
   ▼
5. 前端展示审批面板 → 用户编辑聚合稿 + 补充 Human Guidance → 点击审批
   ▼
6. finalize_summary() 启动 Phase 2 LangGraph
   │
   ├─ fusion_drafter_node
   │    → draft_summary (Markdown)
   │    → revision_count++
   │
   ├─ hallucination_grader_node
   │    ├─ hallucination_score = "yes" → feedback_instructions → 回流 fusion_drafter
   │    └─ hallucination_score = "no"  → 继续
   │
   └─ usefulness_grader_node
        ├─ usefulness_score = "no"  → feedback_instructions → 回流 fusion_drafter
        └─ usefulness_score = "yes" → END
   ▼
7. 返回 draft_summary（最终 Markdown 总结）
   ▼
8. 服务层清理 temp/，前端缓存 active_thread_id
```

#### 时间旅行链路

```
1. 用户输入
   │  thread_id, timestamp ("MM:SS" | "HH:MM:SS"), question, window_seconds
   ▼
2. Checkpointer 读取历史 channel_values
   │  → transcript, keyframes（完整历史状态）
   ▼
3. parse_timestamp_to_seconds(timestamp) → target_seconds
   ▼
4. 证据抽取
   ├─ extract_transcript_window(transcript, target_sec, window) → 时间窗内语音文本
   └─ find_nearest_keyframe(keyframes, target_sec) → 最近邻关键帧 + base64 恢复
   ▼
5. 构建证据约束提示 → LLM 生成回答
   ▼
6. 返回回答（降级时返回结构化证据片段 + 降级原因）
```

#### 状态透传链路

```
LangGraph stream(stream_mode="updates")
  │  每个节点执行后发出状态更新事件
  ▼
api.py 中的事件循环
  │  解析事件，构造 [[PROGRESS]] JSON payload
  │  或节点级状态消息
  ▼
status_callback(msg) 闭包
  │  由 VideoSummaryService 注入并逐层传递
  ▼
app.py 中的 update_status_ui() 闭包
  │  解码 [[PROGRESS]] → 更新分片进度条
  │  文本消息 → 更新 st.status label + 写入日志
  ▼
Streamlit 前端实时渲染
```

### 5.5 设计模式汇总

| 模式 | 应用位置 | 目的 |
|------|----------|------|
| **模板方法** | `core/extraction/base.py` | 固定提取流程，子类仅实现视频获取差异 |
| **策略** | `UrlVideoSource` / `LocalFileVideoSource` | 不同视频来源的获取策略可互换 |
| **工厂** | `checkpoint_factory.py` | Checkpointer 创建 + 内存缓存，支持多后端 |
| **自定义 Reducer** | `state.py` | LangGraph 并行分支的深度状态合并 |
| **条件路由** | `edges/router.py` | 质检闭环回流决策 |
| **Send API fan-out** | `graph.py` + `map_dispatcher.py` | 动态数量的并行任务分发 |
| **两阶段状态机** | `graph.py` | 分析 → 审批 → 终稿的工作流拆分 |
| **闭包注入** | `app.py` → `status_callback` | 实时状态透传，无需修改节点签名 |
| **环境变量注入** | `workflow_service.py` | 解耦 LangGraph 节点对运行时凭证的依赖 |
| **优雅降级** | 多个节点 + 搜索工具 | 无 API Key / 网络异常时不崩溃，返回结构化降级结果 |
