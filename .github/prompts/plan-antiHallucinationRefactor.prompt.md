# 解决架构短板：防幻觉深度重构计划

从 Strict Grounding、全局大纲自举、音频制导子图、外部搜索剥离、结构化聚合五维升级。核心架构变化是引入 `Chunk_Subgraph`（audio → vision 顺序流水线），删除 synthesizer 层，同时为聚合器和 fusion drafter 引入时间戳锚点机制。

---

## Phase A — 基础状态契约（所有后续 Phase 的依赖）

**A1** — 修改 `core/workflow/video_summary/state.py`
- 新增 `narrative_arc: Optional[List[Dict]]` 字段（按时间轴划分的故事线章节，结构：`[{chapter_id, title, start_sec, end_sec, summary}]`）
- **移除** `structured_global_context` 字段（全局上下文职责统一由 `narrative_arc` 承担，不再需要实体列表 + 时间锚点的复合结构）

**A2** — 新建 `core/workflow/video_summary/nodes/chunk_state.py`
- 定义 `ChunkState` TypedDict，用于子图内部流转
- 字段：`chunk_id`, `transcript_segments`, `keyframe_indexes`, `keyframes`, `keyframes_base_path`, `narrative_arc`, `previous_chunk_summaries`, `transcript_claims`, `frame_references`, `chunk_summary`, `modality_status`, `latency_ms`
- **不包含** `audio_insights`、`vision_insights`（旧散文字段，本次彻底移除）
- **不包含** `structured_global_context`（已由 `narrative_arc` 替代）

---

## Phase B — 全局大纲 LLM 升级（依赖 A1）

**B1** — 修改 `core/workflow/video_summary/nodes/outline_bootstrap.py`
- 现有：仅提取 `entities` + `timeline_anchors`（确定性，无 LLM）
- 新增：在现有逻辑后调用轻量 LLM（`get_model_for_capability("chat")`），输入完整 transcript，提取 `narrative_arc`（按时间轴划分的叙事章节列表）
- 提示词约束：禁止臆造情节，必须严格基于 transcript 内容，JSON 输出
- 写入 `state.narrative_arc`；**不再输出 `structured_global_context`**（移除该字段的填充逻辑）
- 失败时降级：`narrative_arc = []`，不阻断主流程

---

## Phase C — 删除外部搜索（Phase D 的前置）

**C1** — 删除 `core/workflow/video_summary/tools/search_tools.py`

**C2** — 删除 `tests/core/generation/tools/test_search_tools.py`

---

## Phase D — Worker 节点重构：Strict Grounding（依赖 A、B、C）

**D1** — 修改 `core/workflow/video_summary/nodes/chunk_audio_analyzer.py`
- 删除 Tavily ReAct 循环（所有 `execute_tavily_search` 调用与相关 tool_call 逻辑）
- 改造输出协议（仅输出新字段，删除旧散文字段）：
  - **删除** `audio_insights`、`audio_structured_analysis` 输出
  - 输出 `transcript_claims: List[Dict]`，每条结构为 `{claim, exact_quote, timestamp}`（不命名 verified 是因为此时还未经过视觉交叉验证）
- 注入 `narrative_arc`：从 ChunkState 读取，注入提示词，要求 worker 严格参照大纲章节定位当前分片

**D2** — 修改 `core/workflow/video_summary/nodes/chunk_vision_analyzer.py`
- 删除 Tavily ReAct 循环
- 新增输入：读取同一分片的 `transcript_claims`（由子图顺序保证）
- 改造输出协议（仅输出新字段，删除旧散文字段）：
  - **删除** `vision_insights`、`vision_structured_analysis` 输出
  - 输出 `frame_references: List[Dict]`，每条结构为 `{frame_time, observation, audio_claim_match: "confirmed|absent|contradicted"}`
  - 输出 `chunk_summary`（融合了音频事实 + 画面验证，synthesizer 不再需要）
- 注入 `narrative_arc`：同 D1，严格参照大纲定位分片

**D3** — 清理旧字段全局消费方（与 D1/D2 同步执行）
- `api.py`：`_emit_chunk_progress` 进度追踪更新
  - `audio_done_ids`：改为检测 `transcript_claims` 非空（替代原 `audio_insights` 非空判断）
  - `vision_done_ids`：改为检测 `frame_references` 非空（替代原 `vision_insights` 非空判断）
  - 删除 `synthesis_done_ids` 追踪（synthesizer 层已移除，进度模型简化为双通道）
- `state.py`：`_merge_chunk_results` 中删除对 `audio_insights`/`vision_insights` 的显式合并分支，改为对 `transcript_claims`（list append）和 `frame_references`（list append）的合并
- `map_dispatcher.py`：删除 `_modality_ready()` 函数（依赖旧字段且随 `synthesis_barrier_node` 一并废弃）；`_is_chunk_synthesized()` 保留，但去除对 `audio_insights`/`vision_insights` 的引用；`route_chunk_subgraph_tasks` Send payload 中删除 `structured_global_context` 注入，仅保留 `narrative_arc`

---

## Phase E — 子图架构重构（依赖 A2、D1、D2）

**E1** — 修改 `core/workflow/video_summary/nodes/map_dispatcher.py`
- **新增** `wave_gate_node(state) -> dict`：
  - 吸收原 `chunk_synthesizer_node` 的 chunk_results 按 chunk_plan 排序逻辑
  - 维护 `chunk_summary_memory`（滑动窗口）
  - 触发 `route_after_wave_synthesis` 路由判断
- **新增** `route_chunk_subgraph_tasks(state) -> List[Send]`：
  - 替换原 `route_audio_send_tasks` + `route_vision_send_tasks` + `route_synthesis_send_tasks`
  - 对每个 active_wave chunk 发送 `Send("chunk_subgraph_node", ChunkState(...))`
  - 在 Send payload 中注入 `previous_chunk_summaries`（滑动窗口）、`narrative_arc`
- **删除** `synthesis_barrier_node`、`route_audio_send_tasks`、`route_vision_send_tasks`、`route_synthesis_send_tasks`、`route_synthesis_bypass_if_ready`（共 5 个函数）

**E2** — 修改 `core/workflow/video_summary/graph.py`
- **新增** `build_chunk_subgraph()` 函数：
  - 状态类型：`ChunkState`
  - 拓扑：`START → chunk_audio_worker_node → chunk_vision_worker_node → END`
  - 编译为 `chunk_subgraph_compiled`
- **重构** `build_video_summary_graph()`：
  - 移除节点：`synthesis_barrier_node`、`chunk_synthesizer_worker_node`、`chunk_synthesizer_node`
  - 新增节点：`chunk_subgraph_node`（= `chunk_subgraph_compiled`）、`wave_gate_node`
  - 移除 conditional_edges：`route_audio_send_tasks`、`route_vision_send_tasks`、`route_synthesis_bypass_if_ready`
  - 新增 conditional_edge：`map_dispatch_node` → `route_chunk_subgraph_tasks`

新 Phase 1 前半拓扑：
```
START
  → chunk_planner_node
  → outline_bootstrap_node          ← 新增 narrative_arc 输出
  → data_preparation_node
  → map_dispatch_node
      → route_chunk_subgraph_tasks  ← [fan-out Send × N]
      → chunk_subgraph_node         ← audio → vision 顺序，per chunk
      → wave_gate_node              ← [fan-in]
      → route_after_wave_synthesis
          ├─ CONTINUE_WAVE → map_dispatch_node
          └─ WAVE_DONE    → chunk_aggregator_node
  → human_gate_node
  → END (pending_human_review)
```

**E3** — 删除 `core/workflow/video_summary/nodes/chunk_synthesizer.py`

---

## Phase F — 聚合器与 Fusion Drafter 升级（依赖 A1、D1、D2）

**F1** — 修改 `core/workflow/video_summary/nodes/chunk_aggregator.py`
- 引入 `narrative_arc`：从 state 读取章节列表
- 字段消费角色明确：
  - **主路径**：使用 `transcript_claims`（原子事实 + 引用）和 `frame_references`（画面验证）构建结构化 Markdown
  - **降级回退**：`narrative_arc` 为空时改用 `chunk_summary`（vision worker 产出的叙事融合稿）平铺，保持鲁棒性
  - `chunk_summary` 在主路径中**不参与**聚合正文，仅作降级兜底，避免两份叙事并存造成 Fusion Drafter 读取歧义
- 主路径输出结构：
  ```markdown
  # 章节标题 [start - end]
  ## chunk_0 [00:00:00 - 00:08:00]
  - [02:15] claim ("exact_quote")  ← transcript claim (audio worker)
    - 🖼 frame_time: observation [confirmed]  ← visual verification
  ```

**F2** — 修改 `core/workflow/video_summary/nodes/fusion_drafter.py`
- 系统提示新增：每个实质性陈述句末须附 `[HH:MM]` 时间戳引用
- 系统提示新增：对于生僻专业术语，在文末生成【名词解释附录】，基于自身知识库，禁止臆测

---

## Phase G — 配置更新（可与其他 Phase 并行）

**G1** — 修改 `config/settings.py`
- `AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS` 默认值：`"24000"` → `"100000"`

**G2** — 同步修改 `.env_example`
- 注释行 `AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS=24000` → `AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS=100000`

---

## Phase H — 测试更新（依赖 D~F 全部完成）

**H1** — 更新 `tests/core/generation/test_chunk_workers_structured.py`
- 断言新输出字段：`transcript_claims` 列表、`frame_references` 列表
- **断言旧字段不存在**：chunk 结果中不应再出现 `audio_insights`、`vision_insights` 键
- 断言 prompt 包含 `narrative_arc` 章节注入
- 删除 Tavily ReAct 相关断言

**H2** — 更新 `tests/core/generation/test_map_dispatcher.py`
- 测试 `route_chunk_subgraph_tasks()` 正确生成 Send 列表
- 测试 `wave_gate_node` 排序逻辑（替代原 `chunk_synthesizer_node` 相关测试）
- 删除 `synthesis_barrier_node`、`route_synthesis_send_tasks`、`route_synthesis_bypass_if_ready` 的测试

**H3** — 重写 `tests/integration/test_synthesis_send_api_flow.py`
- 基于新子图拓扑（audio → vision 顺序执行）
- 验证 vision worker 能读取到 `transcript_claims`（audio worker 产出的断言列表）
- 验证 `chunk_summary` 由 vision worker 生成而非 synthesizer
- 验证 chunk 结果中**不包含** `audio_insights`、`vision_insights` 键

**H4** — 更新 `tests/integration/test_graph_level_parallelism.py`
- 适配新图拓扑（`chunk_subgraph_node` 替代并行 audio+vision）
- 验证波次 fan-out → fan-in 仍然正确

**H5** — 更新 `tests/core/generation/test_outline_bootstrap.py`
- Mock LLM 调用，验证 `narrative_arc` 写入 `state.narrative_arc` 与 `structured_global_context`
- 验证 LLM 失败时 `narrative_arc = []` 降级

**H6** — 新增 `tests/core/generation/test_chunk_subgraph_flow.py`
- 验证子图内 audio → vision 顺序执行（vision 接收到 `transcript_claims`，而非已删除的 `audio_insights`）
- 验证 `transcript_claims` 从 audio worker 流入 vision worker（ChunkState 内部流转）
- 验证 `chunk_summary` 由 vision worker 生成而非 synthesizer

---

## 验证命令（按 Phase 顺序）

```powershell
# Phase B
pytest tests/core/generation/test_outline_bootstrap.py -v

# Phase D
pytest tests/core/generation/test_chunk_workers_structured.py -v

# Phase E
pytest tests/core/generation/test_map_dispatcher.py -v
pytest tests/core/generation/test_chunk_subgraph_flow.py -v

# Phase E+F 集成
pytest tests/integration/test_synthesis_send_api_flow.py tests/integration/test_graph_level_parallelism.py -v

# 全量回归（E2E 除外）
pytest tests/ -q --ignore=tests/integration/test_e2e_pipeline.py
```

---

## 边界约束（不动项）

- **`structured_global_context` 已移除**：`VideoSummaryState` 中该字段删除，`outline_bootstrap_node` 不再填充该字段，全局上下文一律从 `narrative_arc` 读取
- **HITL 审批**：`human_gate_node` 位置与逻辑不变
- **Self-RAG 质检闭环**：`hallucination_grader_node` + `usefulness_grader_node` 不动
- **Time Travel 追问**：`answer_question_at_timestamp` 不动
- **`chunk_results` reducer**：`_merge_chunk_results` 保留（新增字段向后兼容）
- **波次分发**：`WAVE_DISPATCH_SIZE` 机制保留，仅路由从三路→一路
- **worker 降级标记**：`<missing_context>` 机制保留
- **Phase 2 图**：`build_finalization_graph` 完全不动

---

## 不可逆操作清单（执行前需确认）

| 操作 | 文件 |
|------|------|
| DELETE | `core/workflow/video_summary/tools/search_tools.py` |
| DELETE | `core/workflow/video_summary/nodes/chunk_synthesizer.py` |
| DELETE | `tests/core/generation/tools/test_search_tools.py` |
