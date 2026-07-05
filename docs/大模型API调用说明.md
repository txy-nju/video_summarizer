# 大模型 API 调用全景说明

> 本文档梳理项目中所有调用大模型 API 的位置、调用方式、参数配置与用途。

---

## 目录

1. [架构概览](#1-架构概览)
2. [LLM 抽象层](#2-llm-抽象层-corellm)
3. [视频总结工作流](#3-视频总结工作流-coreworkflowvideo_summary)
4. [QA 智能体](#4-qa-智能体-coreagent)
5. [音频转录](#5-音频转录-coreextractioninfrastructure)
6. [离线评估（LLM-as-Judge）](#6-离线评估llm-as-judge-evaluation)
7. [Embedding 向量化](#7-embedding-向量化-backendinfrastructure)
8. [可观测性](#8-可观测性-backendobservability)
9. [连通性检查脚本](#9-连通性检查脚本-scripts)
10. [环境变量速查表](#10-环境变量速查表)

---

## 1. 架构概览

项目采用 **provider-agnostic（厂商无关）** 的 LLM 抽象架构，所有大模型调用统一经过以下路径：

```
调用方（workflow nodes / agents / evaluation）
    │
    ├── 通过 capability 名称获取模型实例
    │   get_model_for_capability("chat" | "vision" | "transcribe" | "rag")
    │
    ├── 配置解析层 resolve_provider / resolve_model_name / resolve_api_key
    │   按环境变量自动路由到具体厂商
    │
    └── BaseModel 具体实现
        ├── OpenAIModel    (chat + vision + transcribe via Whisper)
        ├── DeepSeekModel  (chat + vision)
        ├── QwenModel      (chat + vision + transcribe via paraformer)
        ├── GeminiModel    (chat + vision)
        ├── GroqModel      (chat + transcribe via Whisper)
        ├── AigcModel      (chat + vision + transcribe via vivo lasr 5-phase)
        └── LocalModel     (chat + vision + transcribe, 本地服务)
```

**4 种 capability：**

| capability | 用途 | 典型模型 | 专用环境变量 |
|---|---|---|---|
| `chat` | 纯文本对话 | gpt-4o / deepseek-chat | `CHAT_PROVIDER`, `CHAT_API_KEY`, `CHAT_MODEL_NAME` |
| `vision` | 多模态（文本+图像） | gpt-4o / qwen-vl-max | `VISION_PROVIDER`, `VISION_API_KEY`, `VISION_MODEL_NAME` |
| `transcribe` | 音频转文字 | whisper-1 / paraformer-v2 | `TRANSCRIBE_PROVIDER`, `TRANSCRIBE_API_KEY`, `TRANSCRIBE_MODEL_NAME` |
| `rag` | RAG 问答 | gpt-4o | `RAG_PROVIDER`, `RAG_API_KEY`, `RAG_MODEL_NAME` |

---

## 2. LLM 抽象层 (`core/llm/`)

### 2.1 基类接口 `BaseModel` — `core/llm/base.py`

三个抽象方法，所有 provider 必须实现：

| 方法 | 返回类型 | 说明 |
|---|---|---|
| `chat_completion(model, messages, temperature, response_format, max_tokens, timeout)` | `str` | 单次对话补全，返回完整文本 |
| `stream_chat_completion(model, messages, max_tokens)` | `Iterator[str]` | 流式对话补全，逐 token yield |
| `transcribe_audio(model, audio_path, response_format)` | `TranscriptionResult` | 音频转文字 |

能力声明字段：`supports_transcribe`、`max_audio_upload_bytes`、`audio_chunk_size_bytes`

### 2.2 配置解析 — `core/llm/config.py`

```python
resolve_provider(capability)   # "chat" → "openai" / "deepseek" / ...
resolve_model_name(capability) # "chat" → "gpt-4o" / "deepseek-chat" / ...
resolve_api_key(capability)    # 按优先级链查找对应的 API Key
resolve_base_url(capability)   # 按优先级链查找对应的 Base URL
```

所有函数遵循**多级 fallback 链**，例如 `resolve_api_key("chat")`:
```
CHAT_API_KEY → AIGC_API_KEY → GEMINI_API_KEY → QWEN_API_KEY
→ DEEPSEEK_API_KEY → LOCAL_API_KEY → OPENAI_API_KEY
```

### 2.3 工厂 — `core/llm/factory.py`

```python
get_model_for_capability(capability) -> BaseModel
get_model_name_for_capability(capability) -> str
```

根据 `resolve_provider()` 的返回值实例化对应的 `BaseModel` 子类。

### 2.4 Provider 实现

| 文件 | 类 | chat | vision | transcribe | 特点 |
|---|---|---|---|---|---|
| `openai_model.py` | `OpenAIModel` | ✅ | ✅ | ✅ Whisper | 标准 OpenAI 兼容 |
| `deepseek_model.py` | `DeepSeekModel` | ✅ | ✅ | ❌ | DeepSeek API |
| `qwen_model.py` | `QwenModel` | ✅ | ✅ | ✅ paraformer | 通义千问 / 阿里云 |
| `gemini_model.py` | `GeminiModel` | ✅ | ✅ | ❌ | Google Gemini |
| `groq_model.py` | `GroqModel` | ✅ | ❌ | ✅ Whisper | Groq 高速推理 |
| `aigc_model.py` | `AigcModel` | ✅ | ✅ | ✅ lasr 5-phase | vivo 蓝心大模型 |
| `local_model.py` | `LocalModel` | ✅ | ✅ | ✅ | 本地部署服务 |

### 2.5 RAG 流式封装 — `core/llm/rag_llm.py`

`RagStreamLLM` 将 `BaseModel.stream_chat_completion()` 适配为 RAG 语义接口：

| 方法 | 说明 | 模型参数 |
|---|---|---|
| `stream_text(question, results, messages, max_tokens=1024)` | 纯文本 RAG 流式回答 | 底层 `stream_chat_completion` |
| `stream_multimodal(question, results, frames, messages, max_tokens=1024)` | 多模态 RAG（文本+帧图像），失败自动降级为 `stream_text` | 底层 `stream_chat_completion` |

工厂方法：
- `RagStreamLLM.from_rag_settings(settings)` — 从 modular_rag settings 构造
- `RagStreamLLM.from_env()` — 从环境变量构造（capability="rag"）

**帧处理逻辑：**
- 用户上传图片：编码为 base64 发送
- 知识库参考帧：有用户图片时仅文本引用（不发送图像），无用户图片时正常发送 base64
- 所有帧读取失败 → 自动降级为纯文本模式

---

## 3. 视频总结工作流 (`core/workflow/video_summary/`)

两阶段流水线中共有 **5 个节点** 直接调用 LLM：

### 3.1 调用总览

| 节点 | 文件 | capability | temperature | 特殊参数 | 容错策略 |
|---|---|---|---|---|---|
| Outline Bootstrap | `nodes/outline_bootstrap.py` | `chat` | 0.2 | — | 静默降级为 `[]` |
| Chunk Multimodal Worker | `nodes/chunk_multimodal_analyzer.py` | `vision` | 0.2 | max_tokens=2000, timeout | 重试 + 降级标记 |
| Fusion Drafter | `nodes/fusion_drafter.py` | `chat` | 0.5 | — | 上抛异常 |
| Hallucination Grader | `nodes/hallucination_grader.py` | `chat` | 0.0 | JSON Mode | 熔断放行 |
| Usefulness Grader | `nodes/usefulness_grader.py` | `chat` | 0.0 | JSON Mode | 熔断放行 |

### 3.2 详细说明

#### 3.2.1 Outline Bootstrap — `_extract_narrative_arc_llm()`

```
文件: core/workflow/video_summary/nodes/outline_bootstrap.py:330
```

- **用途**：从完整 transcript 中提取叙事章节大纲（narrative_arc），为后续分片分析提供全局语义定位
- **调用链**：`outline_bootstrap_node()` → `_extract_narrative_arc_llm()` → `model_client.chat_completion()`
- **输入**：带时间戳的完整转录文本（`[start_sec] text` 格式）
- **输出**：JSON 数组 `[{chapter_id, title, start_sec, end_sec, summary}]`
- **容错**：LLM 调用失败时静默降级为 `[]`，不阻断主流程
- **Prompt 要点**：禁止臆造，时间必须与原文对齐，直接输出 JSON 数组（无 markdown 包裹）

#### 3.2.2 Chunk Multimodal Worker — `_llm_multimodal_analyze()`

```
文件: core/workflow/video_summary/nodes/chunk_multimodal_analyzer.py:103
```

- **用途**：对每个视频分片进行**原生多模态分析**——同时读入 transcript 文本和关键帧图像，输出结构化 JSON
- **调用链**：`chunk_multimodal_worker_node()` → `_run_multimodal_with_retry()` → `_llm_multimodal_analyze()` → `model_client.chat_completion()`
- **输入**：
  - System Prompt（132 行详细指令）
  - User Content：`[chunk_id]` + `[user_prompt]` + `[narrative_arc]` + `[previous_chunk_summaries]` + `[chunk_transcript]` + 最多 15 帧 base64 图像（均匀采样）
- **输出**：`{chunk_summary, chunk_insights_md}`
- **帧采样**：最多 15 帧，均匀采样避免只取开头
- **重试**：`CHUNK_WORKER_MAX_RETRIES` 次（默认 1），超时 `CHUNK_WORKER_TIMEOUT_SECONDS`（默认 45s）
- **降级**：重试耗尽后输出 `[CHUNK_DEGRADED_MARKER]:multimodal:{error_type}:retries_exhausted`

#### 3.2.3 Fusion Drafter — `fusion_drafter_node()`

```
文件: core/workflow/video_summary/nodes/fusion_drafter.py:7
```

- **用途**：全局成文节点，将聚合后的所有分片证据合成为一篇 Markdown 总结报告
- **调用链**：`fusion_drafter_node()` → `model_client.chat_completion()`
- **temperature=0.5**（工作流中最高，为获得更好的行文组织能力）
- **输入**：System Prompt（含架构级约束：证据优先、时间一致性、专业排版）+ `aggregated_chunk_insights` + `user_prompt`
- **改写支持**：当 `feedback_instructions` 非空时，在 System Prompt 中附加强制修改指令
- **人类审批支持**：当 `human_guidance` 非空时，作为最高优先级指令注入
- **容错**：异常上抛，由编排层标记 FAILED 并触发 Celery 重试

#### 3.2.4 Hallucination Grader — `hallucination_grader_node()`

```
文件: core/workflow/video_summary/nodes/hallucination_grader.py:9
```

- **用途**：Self-RAG 第一道防线——对比 draft_summary 与源数据，判断是否有幻觉捏造
- **调用链**：`hallucination_grader_node()` → `model_client.chat_completion()`
- **JSON Mode**：`response_format={"type": "json_object"}`, **temperature=0.0**
- **二级证据约束**：
  - 一级证据（最终裁决）：`aggregated_chunk_insights`
  - 二级约束（辅助信号）：`structured_global_context`（entities + timeline_anchors）
- **输出**：`{score: "yes"|"no", faulty_timestamp, reason}`
- **熔断**：`revision_count >= SELF_RAG_MAX_REVISIONS`（默认 2）强制放行
- **异常兜底**：JSON 解析失败或 API 异常 → 放行（`hallucination_score="no"`）

#### 3.2.5 Usefulness Grader — `usefulness_grader_node()`

```
文件: core/workflow/video_summary/nodes/usefulness_grader.py:9
```

- **用途**：Self-RAG 第二道防线——检查草稿是否真正回应了用户需求
- **调用链**：`usefulness_grader_node()` → `model_client.chat_completion()`
- **JSON Mode**：`response_format={"type": "json_object"}`, **temperature=0.0**
- **证据边界约束**：修改指令必须限定在 `aggregated_chunk_insights` 范围内，禁止要求凭空补充
- **输出**：`{score: "yes"|"no", reason}`
- **熔断**：同 Hallucination Grader

---

## 4. QA 智能体 (`core/agent/`)

### 4.1 QAAgent — `core/agent/qa_agent.py`

**知识库问答的 ReAct Agent**

```
调用链: QAAgent.answer_stream()
  → RagStreamLLM._model.stream_chat_completion()
  → BaseModel.stream_chat_completion()
```

| 属性 | 值 |
|---|---|
| 调用方式 | **流式** `stream_chat_completion` |
| 底层 capability | `rag`（通过 `RagStreamLLM.from_env()`） |
| 最大迭代 | 3 轮 ReAct（THOUGHT → ACTION → Observation → ...） |
| 多模态 | ✅ 支持用户上传图片（base64 编码） |
| 系统提示词 | `_DEFAULT_SYSTEM_PROMPT` + 动态注入工具列表 |
| 进度事件 | thinking → searching → retrieved → generating |

**ReAct 循环：**
1. 构建 messages（system prompt + conversation history + current question）
2. 流式调用 LLM → 获取 THOUGHT + ACTION/FINAL_ANSWER
3. 若 ACTION=rag_search → 执行检索 → 注入 Observation → 回到步骤 2
4. 若 FINAL_ANSWER → 流式输出给用户
5. 超过 3 轮 → 强制生成最终回答

### 4.2 VideoQAAgent — `core/agent/video_qa_agent.py`

**视频专属问答 Agent，支持 RAG 和 Timestamp 两种模式**

```
调用链: VideoQAAgent.answer_stream_with_context()
  → _answer_rag() / _answer_timestamp()
  → RagStreamLLM._model.stream_chat_completion()
  → BaseModel.stream_chat_completion()
```

| 模式 | 方法 | 说明 |
|---|---|---|
| `rag` | `_answer_rag()` | HybridSearch + Reranker → 构建多模态 messages → 流式 LLM |
| `timestamp` | `_answer_timestamp()` | Checkpoint 恢复 → 提取时间窗证据 → 构建多模态 messages → 流式 LLM |

**RAG 模式流程：**
1. `HybridSearch + Reranker` 检索 top_k=5
2. 分离用户上传图片与知识库参考帧
3. 构建多模态 messages（文本上下文 + 帧图片 base64）
4. 流式 LLM 生成回答

**Timestamp 模式流程：**
1. 从 checkpoint 恢复会话状态
2. `find_nearest_keyframe()` + `extract_transcript_window()` 提取目标时间窗证据
3. 用户上传图片时：LLM 只接收用户图片，视频帧仅文本引用（物理隔离）
4. 无用户图片时：正常发送视频帧 base64
5. 流式 LLM 生成回答
6. 异常降级：返回证据片段（不调用 LLM）

**容错**：无 API Key 时降级为返回证据文本

---

## 5. 音频转录 (`core/extraction/infrastructure/`)

### 5.1 AudioTranscriber — `transcriber.py`

```
调用链: AudioTranscriber.transcribe()
  → get_model_for_capability("transcribe").transcribe_audio()
  → BaseModel.transcribe_audio()
```

| 属性 | 值 |
|---|---|
| capability | `transcribe` |
| 默认模型 | OpenAI: `whisper-1`, AIGC: `fileasrrecorder`, Qwen: `paraformer-v2`, Groq: `whisper-large-v3-turbo` |
| 返回格式 | `verbose_json`（含时间戳分段） |
| 大文件处理 | 超过 `max_audio_upload_bytes` 时自动切段，逐段转录后合并 |
| 切段策略 | 按 provider 声明的限制自动切分，确保每段不超过限制 |
| 超时 | OpenAI Whisper: 120s；AIGC lasr: 600s 轮询 |

**AIGC lasr 5 阶段流程（`AigcModel.transcribe_audio()`）：**
1. `POST /lasr/create` — 创建音频资源
2. `POST /lasr/upload` — 5MB 分片上传
3. `POST /lasr/run` — 创建转写任务
4. `POST /lasr/progress` — 指数退避轮询至 100%
5. `POST /lasr/result` — 获取转写结果

---

## 6. 离线评估/LLM-as-Judge (`evaluation/`)

> 评估模块**不经过** `core/llm/` 抽象层，直接使用 `openai.OpenAI()` 客户端。

### 6.1 `evaluation/llm_as_a_judge.py`

**5 个 LLM 调用函数，全部使用 JSON Mode + temperature=0.0：**

| 函数 | 用途 | 输出结构 |
|---|---|---|
| `extract_claims()` | 从总结文本中抽取可验证的独立事实声明 | `{claims: [{claim_id, claim_text, claim_type, importance}]}` |
| `verify_claims()` | 逐条核查 claim 是否被参考资料支持 | `{verifications: [{claim_id, status, hallucination_type, confidence, evidence, reason}]}` |
| `score_claim_based_hallucination()` | 组合 extract + verify + 加权评分 | 幻觉评分（Fact 维度） |
| `extract_task_requirements()` | 从 user_prompt + human_guidance 抽取可评估的任务要求 | `{requirements: [{requirement_id, requirement_text, importance}]}` |
| `score_task_alignment()` | 评估总结是否满足任务要求 | 任务对齐评分（Task 维度） |

**评分公式：**
- **Fact 评分**：`0.6 × support_ratio + 0.25 × (1 - capped_hallucination_density) + 0.15 × confidence`
- **Task 评分**：`0.85 × coverage_ratio + 0.15 × confidence`
- **通过阈值**：≥0.75 pass, ≥0.55 warn, <0.55 fail

**幻觉类型权重：**
- entity（实体幻觉）：0.2
- relation_action（关系/行为幻觉）：0.5
- fabrication（凭空捏造）：1.0

### 6.2 `evaluation/qa_judge.py`

**时间旅行追问质量评估：**

| 函数 | 用途 |
|---|---|
| `evaluate_qa_answer()` | 从 4 维度评估 Q&A 回答质量 |

**4 维度权重：**
- relevance（相关性）：0.30
- accuracy（准确性）：0.35
- completeness（完整性）：0.20
- temporal_precision（时间精度）：0.15

---

## 7. Embedding 向量化 (`backend/infrastructure/`)

### 7.1 `rag_settings_factory.py` — `build_rag_settings()`

```
调用方式: openai.OpenAI().embeddings.create()
          → 由 modular_rag 库内部调用
```

| 属性 | 值 |
|---|---|
| provider | `openai`（硬编码） |
| 默认模型 | `text-embedding-3-small`（可通过 `RAG_EMBEDDING_MODEL` 覆盖） |
| API Key | `OPENAI_API_KEY` |
| Base URL | `OPENAI_BASE_URL` |
| 调用场景 | 视频转录文本分块后的向量化，存入 Chroma 向量库 |

---

## 8. 可观测性 (`backend/observability/`)

### 8.1 `llm_tracing.py` — `trace_llm_call()`

**Context Manager**，为 LLM API 调用包裹 OpenTelemetry Span：

```python
with trace_llm_call(provider="openai_compatible", model=model_name,
                    scope="chunk_multimodal_worker", scope_id=chunk_id,
                    workflow_state="ANALYSIS"):
    raw_content = model_client.chat_completion(...)
```

**使用位置**（3 个 workflow node 中）：
- `chunk_multimodal_analyzer.py` — scope: `chunk_multimodal_worker`
- `fusion_drafter.py` — scope: `fusion_drafter`
- `hallucination_grader.py` — scope: `hallucination_grader`
- `usefulness_grader.py` — scope: `usefulness_grader`

**Span 属性**：provider, model, scope, scope_id, task_id, workflow_state, retry_count, error_code

> ⚠️ 注意：prompt content / transcript payload **不会**记录到 span 属性中。

---

## 9. 连通性检查脚本 (`scripts/`)

### 9.1 `check_openai_api.py`

**覆盖全部 5 种 API 调用的连通性诊断工具：**

```bash
python scripts/check_openai_api.py          # 全量检查（5 项）
python scripts/check_openai_api.py --quick   # 仅 Chat 纯文本
```

| 测试项 | capability | 模拟场景 |
|---|---|---|
| Chat Audio Worker 模式 | `chat` | `chunk_multimodal_analyzer` 的纯文本调用 |
| Chat Fusion Drafter 模式 | `chat` | `fusion_drafter` / `grader` 的 JSON Mode 调用 |
| Vision Worker 模式 | `vision` | `chunk_multimodal_analyzer` 的多模态调用 |
| Audio Transcriptions | `transcribe` | `AudioTranscriber.transcribe()` 的转录调用 |
| Embedding（RAG 向量化） | — | `rag_settings_factory` 的 embedding 调用 |
| Tavily Search | — | 搜索工具的独立连通性 |

---

## 10. 环境变量速查表

### 通用 / 兜底

| 变量 | 说明 |
|---|---|
| `OPENAI_API_KEY` | 所有 capability 的最终 fallback API Key |
| `OPENAI_BASE_URL` | 所有 capability 的最终 fallback Base URL |
| `OPENAI_MODEL_NAME` | chat capability 的 fallback 模型名 |
| `OPENAI_VISION_MODEL_NAME` | vision capability 的 fallback 模型名（在 `CHAT_MODEL_NAME` / `OPENAI_MODEL_NAME` 之后） |

### Chat（纯文本对话）

| 变量 | 优先级 |
|---|---|
| `CHAT_PROVIDER` | 最高（默认 openai） |
| `CHAT_API_KEY` | 最高 |
| `CHAT_BASE_URL` | 最高 |
| `CHAT_MODEL_NAME` | 最高 |

### Vision（多模态）

| 变量 | 优先级 |
|---|---|
| `VISION_PROVIDER` | 最高（默认 openai） |
| `VISION_API_KEY` | 最高 |
| `VISION_BASE_URL` | 最高 |
| `VISION_MODEL_NAME` | 最高 |

### Transcribe（音频转录）

| 变量 | 优先级 |
|---|---|
| `TRANSCRIBE_PROVIDER` | 最高（默认 openai） |
| `TRANSCRIBE_API_KEY` | 最高 |
| `TRANSCRIBE_BASE_URL` | 最高 |
| `TRANSCRIBE_MODEL_NAME` / `TRANSCRIBER_MODEL` | 最高 |

### RAG（知识库问答）

| 变量 | 说明 |
|---|---|
| `RAG_PROVIDER` | 默认 fallback 到 `CHAT_PROVIDER` → `openai` |
| `RAG_API_KEY` | |
| `RAG_BASE_URL` | |
| `RAG_MODEL_NAME` | |
| `RAG_EMBEDDING_MODEL` | embedding 模型名，默认 `text-embedding-3-small` |

### 厂商专属

| 厂商 | 变量 |
|---|---|
| DeepSeek | `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL` |
| Qwen | `QWEN_API_KEY`, `QWEN_BASE_URL` |
| Gemini | `GEMINI_API_KEY`, `GEMINI_BASE_URL` |
| Groq | `GROQ_API_KEY`, `GROQ_BASE_URL` |
| AIGC (vivo) | `AIGC_API_KEY`, `AIGC_BASE_URL` |
| Local | `LOCAL_API_KEY`, `LOCAL_BASE_URL` |

### 工作流调参

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CHUNK_WORKER_TIMEOUT_SECONDS` | 45 | 分片 worker 单次 LLM 调用超时 |
| `CHUNK_WORKER_MAX_RETRIES` | 1 | 分片 worker 最大重试次数 |
| `SELF_RAG_MAX_REVISIONS` | 2 | Hallucination / Usefulness Grader 最大回流轮次 |
| `WAVE_DISPATCH_SIZE` | (同 `MAP_MAX_PARALLELISM`) | 每波次派发 chunk 数 |

### 评估

| 变量 | 说明 |
|---|---|
| `EVAL_JUDGE_MODEL` | 评估法官模型，优先级高于 `OPENAI_MODEL_NAME` |

### 搜索

| 变量 | 说明 |
|---|---|
| `TAVILY_API_KEY` | Tavily 搜索 API Key（可选） |

---

## 附录：快速定位表

| 要找什么 | 文件位置 |
|---|---|
| 模型抽象接口 | `core/llm/base.py` |
| 配置/环境变量解析 | `core/llm/config.py` |
| 模型工厂 | `core/llm/factory.py` |
| OpenAI 实现 | `core/llm/openai_model.py` |
| RAG 流式封装 | `core/llm/rag_llm.py` |
| 视频总结-大纲提取 | `core/workflow/video_summary/nodes/outline_bootstrap.py` |
| 视频总结-多模态分片分析 | `core/workflow/video_summary/nodes/chunk_multimodal_analyzer.py` |
| 视频总结-全文成文 | `core/workflow/video_summary/nodes/fusion_drafter.py` |
| 视频总结-幻觉审查 | `core/workflow/video_summary/nodes/hallucination_grader.py` |
| 视频总结-有用性审查 | `core/workflow/video_summary/nodes/usefulness_grader.py` |
| 知识库 QA Agent | `core/agent/qa_agent.py` |
| 视频 QA Agent | `core/agent/video_qa_agent.py` |
| 音频转录 | `core/extraction/infrastructure/transcriber.py` |
| 离线评估-事实核查 | `evaluation/llm_as_a_judge.py` |
| 离线评估-追问评估 | `evaluation/qa_judge.py` |
| Embedding 配置 | `backend/infrastructure/rag_settings_factory.py` |
| LLM 调用 Tracing | `backend/observability/llm_tracing.py` |
| API 连通性检查 | `scripts/check_openai_api.py` |

