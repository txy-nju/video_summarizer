# 项目技术知识库 — 面试官参考手册

> 本文件供面试官（AI Agent）使用，包含本项目的关键实现细节、高频面试题及参考答案。
> 面试过程中用于生成精准追问和评估候选人回答质量。
> **项目**：多模态视频智能总结系统（Video Summarizer）

---

## 模块一：LangGraph StateGraph 工作流设计

### 核心实现
- **两阶段图**：Phase 1（analyze_graph）并行分析所有分片 → Phase 2（finalize_graph）融合输出最终摘要
- **节点类型**：`outline_bootstrap`（结构化全局上下文）、`chunk_audio_worker`（音频分片分析）、`chunk_vision_worker`（视觉分片分析）、`synthesis_barrier`（波次屏障）、`chunk_synthesizer_worker`（单片融合）、`chunk_synthesizer`（波次推进）、`chunk_aggregator`（并行结果聚合）、`human_gate`（HITL 人类审核）、`fusion_drafter`（融合全文起草）、`hallucination_grader`（幻觉检测）、`usefulness_grader`（质量检测）
- **条件边（Conditional Edge）**：用于质检回流路由与波次推进路由；质检不通过回到 drafter，达到 `SELF_RAG_MAX_REVISIONS` 后熔断放行
- **自定义 Reducer**：`_merge_chunk_results` 将并行分支的 chunk 结果列表合并到共享 State，解决 Fan-In 写冲突
- **TypedDict State**：`VideoSummaryState` 包含 `transcript`、`keyframes`、`structured_global_context`、`chunk_results`、`chunk_summary_memory`、`draft_summary`、`hallucination_score`、`usefulness_score`、`revision_count` 等字段

### 高频面试题

**Q: 为什么用 LangGraph 而不是普通 Python 函数调用链？**  
A: LangGraph 提供 StateGraph 抽象，将工作流建模为有向图（节点 + 条件边 + 共享状态），原生支持：① 循环（Self-RAG 质检重试）② 并行（Map-Reduce Fan-Out）③ Checkpoint（中断恢复）④ Human-in-the-Loop 中断点。普通函数链无法优雅地处理循环和并行写冲突，需要大量自定义胶水代码。

**Q: VideoSummaryState 里为什么要有 Reducer？_merge_chunk_results 解决什么问题？**  
A: LangGraph 并行执行多个分片节点时，各分支会同时向同一个 State 字段写入结果。默认行为是最后写入覆盖之前的值，导致数据丢失。`_merge_chunk_results` 是自定义 Reducer，声明为 `Annotated[List, _merge_chunk_results]`，每次并行写入时将新结果 append 到列表，实现无损 Fan-In 聚合。

**Q: Phase 1 和 Phase 2 两张图为什么分开，而不是做成一张大图？**  
A: Phase 1（analyze_graph）在 `human_gate` 结束并返回 `review_package`，Phase 2（finalize_graph）读取同一 `thread_id` 的 checkpoint 执行成文与质检闭环。两张图分开后，审批边界、状态恢复和接口职责更清晰，也更便于回归测试与演进。

---

## 模块二：Map-Reduce 分片并行工作流

### 核心实现
- **分片策略**：视频时长 / 分片时长（默认 5 min）→ N 个 chunk，每片独立进行转录+帧分析+摘要合成
- **Fan-Out 实现**：当前采用 **Send API 波次调度**（主架构）
  1. `map_dispatch_node` 按 `WAVE_DISPATCH_SIZE` 派发当前波次分片
  2. `synthesis_barrier_node` 在当前波次就绪后再派发合成 worker
  3. `route_after_wave_synthesis` 决定继续下一波或进入聚合
- **Fan-In**：`chunk_aggregator` 节点接收所有分片结果，通过 `_merge_chunk_results` Reducer 聚合
- **ChunkPlanner**：根据视频时长决定分片数和每片时间范围，存入 State 供下游节点使用

### 高频面试题

**Q: 当前 Send API 波次并行是怎么工作的？**  
A: 系统在图层使用 Send API 做 fan-out/fan-in。`map_dispatch` 仅派发 active wave，audio/vision worker 完成后进入 `synthesis_barrier`，再 fan-out 合成 worker。`chunk_synthesizer_node` 基于 `route_after_wave_synthesis` 决定继续下一波或 wave_done 后进入 `chunk_aggregator`，并由 reducer 解决并行写冲突。

**Q: 如果某个分片的 LLM 调用失败，整个工作流会怎么处理？**  
A: 当前实现在 chunk 节点内部捕获异常，将该分片标记为 error 状态写入 `chunk_results`。`chunk_aggregator` 检测到 error 分片时决定是跳过（只用成功分片的结果）还是整体重试（取决于错误率阈值）。Whisper API 调用使用 `tenacity` 装饰器自动重试（指数退避）。

**Q: 分片合成后如何聚合成全文摘要？chunk_aggregator 做了什么？**  
A: `chunk_aggregator` 将所有分片的 `chunk_summary` 按时间顺序排列，生成结构化的"分片摘要列表"传给 `fusion_drafter`。`fusion_drafter` 接收完整列表，用 LLM 进行跨分片的内容融合、去重、连贯性优化，生成全局 `draft_summary`。

---

## 模块三：Self-RAG 双重质检闭环

### 核心实现
- **双重质检节点**：
  1. `hallucination_grader`：检测 `draft_summary` 是否脱离 `aggregated_chunk_insights` 证据边界（并参考 structured_global_context 约束）
  2. `usefulness_grader`：评估摘要质量，检测内容是否过于简短、重复、缺乏关键信息
- **质检结果路由**：节点输出 `{grade: "pass"/"fail", reason: "..."}`，条件边根据 grade 决策
- **重试机制**：grade = "fail" → 带 `feedback_instructions` 重新进入 `fusion_drafter`；`revision_count` 达到 `SELF_RAG_MAX_REVISIONS` 时熔断放行，避免死循环
- **为什么是两个 Grader**：Hallucination 检测准确性，Usefulness 检测充分性，两者正交——可能不幻觉但摘要质量差（只提了无关内容），也可能摘要丰富但包含幻觉

### 高频面试题

**Q: HallucinationGrader 是怎么判断幻觉的？它会不会误判？**  
A: 节点以 `aggregated_chunk_insights` 为一级证据，并引入 `structured_global_context`（实体/时间锚点）作为二级约束做 JSON 判决。可能误判来源于模型偏差或证据表达歧义，因此系统使用 `SELF_RAG_MAX_REVISIONS` 熔断上限保证活性。

**Q: 质检失败后重新生成时，feedback 是怎么传递的？会不会反复失败？**  
A: 质检节点把定向修改指令写入 `feedback_instructions`，`fusion_drafter` 下一轮读取后执行定点修正。系统通过共享 `revision_count` + `SELF_RAG_MAX_REVISIONS` 控制最大回流次数，避免无限循环。

---

## 模块四：多模态信息提取（Whisper + OpenCV）

### 音频提取 — Whisper
- **模型**：`whisper-1`，调用 OpenAI 语音转录 API
- **输出格式**：`verbose_json`（携带 `segments` 字段，每段含 `start`/`end` 时间戳），保留时序信息供分片时段映射
- **大文件处理**：Whisper API 限制单文件 25MB。超限时采用**递归二分切割**：文件 → 对半切 → 递归判断大小 → 直到每片 ≤ 24MB，再分别转录、按时间戳拼接
- **重试机制**：`@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))` 装饰 Whisper 调用
- **音频提取**：用 `moviepy` 从视频中提取 `.mp3` 音频，临时写入 `temp/audios/`

### 视频帧提取 — OpenCV
- **自适应探针 FPS**：时长 < 5 min → 5 fps；5-30 min → 3 fps；> 30 min → 1 fps（避免短视频帧过多、长视频内存溢出）
- **grab/retrieve 分离**：先批量 `cap.grab()`（仅移动指针，不解码），目标帧再 `cap.retrieve()`（实际解码），减少 CPU/内存开销
- **场景感知去重**：见模块五

### 高频面试题

**Q: Whisper 大文件的递归二分切割是怎么实现的？为什么不直接按固定时长切？**  
A: 用 `moviepy` 的 `subclip()` 递归对半切割，直到每片 < 24MB：`split(file) → if size > 24MB: split(left half) + split(right half)`。不直接按固定时长（如每 10 分钟）切的原因：不同视频的比特率差异巨大（高清 vs 低质量），固定时长不能保证文件大小满足限制；二分切割保证每片一定 ≤ 24MB，无需估算比特率。

**Q: verbose_json 格式里的 segments 有什么用？如何与视频分片对应？**  
A: `segments` 包含每段语音的 `{start, end, text}`。在 Map-Reduce 分片时，系统根据分片的时间范围（如 0-300s）从 `segments` 中筛选对应片段，得到该分片的专属 `transcript_segment`，确保每个分片的 LLM 分析只看自己时间段内的语音，避免跨片混淆。

**Q: grab/retrieve 分离优化解决了什么问题？**  
A: `cap.read()` 等于 `grab() + retrieve()`，会对每一帧都解码。按 3 fps 探针遇到 60fps 视频时，`cap.read()` 仍会解码每一帧（只是丢弃）。`cap.grab()` 只移动文件指针不解码，只在目标帧调用 `cap.retrieve()` 解码，大幅减少 CPU 消耗和不必要的内存分配。

---

## 模块五：场景感知智能抽帧

### 核心实现
- **灰度直方图相关性**：将候选帧转为灰度图 → 计算直方图 → 与前一帧直方图做 `cv2.compareHist(HISTCMP_CORREL)` 比较
- **阈值判定**：相关性 < 0.90 → 场景切换，保留该帧；相关性 ≥ 0.90 → 与前帧高度相似，跳过
- **输出**：去重后的关键帧列表，写入 `temp/frames/`，路径存入 State 的 `frames` 字段
- **设计权衡**：
  - 阈值 0.90 是经验值，对慢速运镜宽容（不抽太多帧），对快速场景切换敏感（不漏关键帧）
  - 纯颜色直方图无法区分构图相似但内容不同的帧（局限性），生产级可引入感知哈希（pHash）

### 高频面试题

**Q: 场景感知抽帧的相关性阈值 0.90 是怎么来的？调大调小各有什么影响？**  
A: 经验值，通过在不同类型视频（讲座、教程、采访、运动）上测试后确定的。调大（如 0.95）：只保留变化极大的帧，容易漏掉重要内容；调小（如 0.80）：抽出大量相似帧，增加后续 LLM 处理成本和 Token 消耗。0.90 在"不漏关键帧"和"控制帧数量"之间平衡。

**Q: 为什么用灰度直方图而不是 RGB 直方图或感知哈希？**  
A: 灰度直方图计算简单、速度快，对光照变化（亮度整体上升/下降）比 RGB 更鲁棒（因为已去除色彩通道相关性）。RGB 直方图对颜色敏感但计算量 3 倍。感知哈希（pHash）对旋转/缩放鲁棒但计算更慢。作为第一版实现，灰度直方图在性价比上是最佳选择；生产级可引入 pHash 作为二级过滤。

---

## 模块六：Checkpoint 持久化与时间旅行

### 核心实现
- **CheckpointFactory**：`get_checkpointer(mode)` 工厂函数 → dev 返回 `InMemorySaver`，prod 返回 `PostgresSaver`
- **thread_id**：每次会话携带唯一 `thread_id`（`{"configurable": {"thread_id": "..."}}}`），同一 `thread_id` 的执行历史可跨请求恢复
- **时间旅行（Time Travel）**：调用 `graph.get_state_history(thread_id)` 获取历史 checkpoint 列表；`graph.update_state(config, values)` 注入修改后的 State；重新调用 `graph.invoke()` 从该 checkpoint 继续执行——实现"回到过去、修改参数、重跑后续节点"

### 高频面试题

**Q: InMemorySaver 和 PostgresSaver 有什么区别？为什么 dev 用内存？**  
A: `InMemorySaver` 将 checkpoint 存在进程内存字典中，重启即丢失，零配置，适合开发和测试。`PostgresSaver` 将所有 checkpoint 持久化到 PostgreSQL，支持跨进程、跨重启的会话恢复，适合生产。dev 用内存是为了快速启动（无需数据库依赖），通过 factory pattern 保证两者接口一致，切换只需改配置。

**Q: 时间旅行功能在用户侧怎么触发？具体流程是什么？**  
A: 用户在 Streamlit 前端点击"追问"功能，输入针对某段内容的追加问题。系统：① 调用 `graph.get_state_history(thread_id)` 列出历史 checkpoint ② 找到 `fusion_drafter` 节点执行前的 checkpoint ③ 将用户追问写入 State 的 `followup_question` 字段 ④ 调用 `graph.update_state()` 注入修改 ⑤ 重新触发 `fusion_drafter` 及后续节点，保留之前的 `transcript`/`frames` 不重新提取。

---

## 模块七：HITL 两阶段工作流

### 核心实现
- **Phase 1 结束点**：`chunk_aggregator` 输出分片摘要列表后，下一个节点是 `human_gate`（HITL 中断点）
- **human_gate 行为**：第一阶段在 `human_gate` 以 pending 结束并返回 `review_package`，由前端触发第二阶段 `finalize_summary` 继续
- **前端展示**：Streamlit 在 `human_gate` 暂停后展示分片摘要列表，提供"继续"/"修改后继续"/"放弃"三个选项
- **Phase 2 触发**：用户点击"继续"后，前端调用 `finalize_summary(thread_id, edited_aggregated_chunk_insights, human_guidance)`，基于同一会话 checkpoint 继续执行 `fusion_drafter` 及后续节点
- **设计价值**：用户在生成全文摘要前可以审查中间结果，减少"全量处理后才发现分析方向不对"的时间浪费

### 高频面试题

**Q: HITL 在当前架构里的实现机制是什么？**  
A: 当前主路径采用“两阶段图 + review_package + thread_id checkpoint”。第一阶段执行到 `human_gate` 结束并返回可编辑内容；用户提交编辑稿与 `human_guidance` 后，第二阶段 `finalize_summary` 在同一会话上继续执行成文与质检闭环。

**Q: 如果用户在 HITL 阶段修改了聚合稿，后续节点怎么感知到？**  
A: 前端将编辑后的聚合稿与指导语作为 `edited_aggregated_chunk_insights` 和 `human_guidance` 传入 `finalize_summary(...)`。`fusion_drafter` 在第二阶段优先读取人工编辑后的聚合稿，并将指导语作为高优先级约束融入最终起草。

---

## 模块八：前端交互 & 实时状态透传

### 核心实现
- **实时状态面板**：`st.status()` 上下文管理器 + `st.write()` 流式输出，每个节点执行时更新面板，用户可实时看到"正在转录…正在分析帧…正在生成摘要…"
- **4 通道进度条**：`st.progress()` 分别追踪 `audio_progress`、`vision_progress`、`synthesis_progress`、`overall_progress`，由 workflow_service 回调更新
- **会话持久化**：`st.session_state` 存储 `thread_id`、历史结果、用户设置，页面刷新后不丢失上下文
- **视频源支持**：① 本地文件上传（`st.file_uploader`） ② URL 输入（yt-dlp 下载）

### yt-dlp 集成
- **功能**：`yt-dlp` 下载 YouTube/B站等平台视频，写入 `temp/videos/`
- **Cookies 支持**：`cookies.txt` 文件注入，支持需要登录的视频
- **为什么不用 youtube-dl**：`yt-dlp` 是 youtube-dl 的活跃 fork，更新频率快、对各平台兼容性更好

### 高频面试题

**Q: st.status 和 st.progress 的状态是怎么从 LangGraph 回调到 Streamlit 的？**  
A: `workflow_service.py` 在调用 `graph.stream()` 时接收每个节点的输出 chunk，通过回调函数将节点执行状态和进度写入 `st.session_state` 中的进度字段。Streamlit 在下一次 rerun 时读取这些字段并更新 UI 组件。由于 Streamlit 不是真正的实时推送（每次 rerun 才刷新），采用 `st.empty()` + 循环 rerun 策略实现"近实时"更新效果。

---

## 模块九：工程化测试体系

### 测试规模
- **~130 个测试**：97 个单元测试 + 33 个集成测试

### 单元测试覆盖模块（97 个）
| 模块 | 文件 | 测试内容 |
|------|------|---------|
| chunk_aggregator | `test_chunk_aggregator.py` | Reducer 合并逻辑、空结果处理 |
| chunk_audio_worker | `test_chunk_workers_structured.py` | 音频 worker 结构化协议与容错 |
| chunk_planner | `test_chunk_planner.py` | 分片数计算、时间范围边界 |
| chunk_synthesizer | `test_chunk_synthesizer.py` | 摘要生成、feedback 注入 |
| chunk_vision_worker | `test_chunk_workers_structured.py` | 视觉 worker 结构化协议与容错 |
| fusion_drafter | `test_fusion_drafter.py` | 融合起草、人工反馈处理 |
| hallucination_grader | `test_hallucination_grader.py` | 幻觉判断路由逻辑 |
| usefulness_grader | `test_usefulness_grader.py` | 质量判断路由逻辑 |
| time_travel | `test_time_travel.py` | Checkpoint 历史查询、State 注入 |
| frame_utils | `test_frame_utils.py` | 灰度直方图相关性计算 |
| graph_routing | `test_graph_routing.py` | 条件边路由决策 |

### 集成测试覆盖（33 个）
| 文件 | 测试场景 |
|------|---------|
| `test_e2e_pipeline.py` | 完整端到端流水线 |
| `test_checkpoint_restore_flow.py` | Checkpoint 保存与恢复 |
| `test_human_review_finalize_flow.py` | HITL 中断 → 恢复全流程 |
| `test_time_travel_pipeline.py` | 时间旅行追问全流程 |
| `test_graph_level_parallelism.py` | Map-Reduce 并行执行 |
| `test_synthesis_send_api_flow.py` | Send API 并发模式 |
| `test_workflow_service_session.py` | WorkflowService 会话管理 |

### Mock 策略
- **单元测试**：用 `unittest.mock.patch` 替换 `openai.OpenAI` 客户端，返回预设 `ChatCompletion` / `Transcription` 对象
- **集成测试**：真实 LangGraph 图执行，但 Mock LLM 调用（控制确定性），测试图拓扑路由和 State 流转
- **设计原则**：单元测试测业务逻辑不测 API，集成测试测图结构不测 LLM 效果

---

## 常见"露馅"警示点

面试中如候选人无法解释以下细节，需在报告中标记：

| 简历描述 | 深挖问题 | 露馅信号 |
|---------|---------|---------|
| "Map-Reduce 分片并行处理" | ChunkPlanner 怎么切片的？Fan-In 写冲突怎么解决？ | 说不清 Reducer / Annotated 机制 |
| "Self-RAG 双重质检" | HallucinationGrader 和 UsefulnessGrader 分别检测什么？ | 两个 grader 职责混淆，或不知道 `SELF_RAG_MAX_REVISIONS` |
| "场景感知智能抽帧" | 阈值 0.90 怎么来的？ | 说不出 cv2.compareHist HISTCMP_CORREL |
| "LangGraph 工作流设计" | VideoSummaryState 里为什么要用 Annotated Reducer？ | 不知道并行写冲突问题 |
| "Checkpoint 持久化" | InMemorySaver 和 PostgresSaver 有什么区别？ | 不知道 CheckpointFactory 或 thread_id |
| "HITL 两阶段工作流" | 第一阶段如何结束？Phase 2 怎么恢复执行？ | 说不清 review_package / finalize_summary / thread_id 复用 |
| "Whisper 大文件处理" | 文件超过 25MB 怎么处理？为什么不按固定时长切？ | 不知道递归二分策略 |
| "单元测试 mock LLM" | 具体 patch 的是哪个对象？ | 说不出 unittest.mock.patch 对 openai 客户端的用法 |
