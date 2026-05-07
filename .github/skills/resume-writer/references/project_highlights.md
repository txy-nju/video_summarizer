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
