# Video Summarizer

一个面向长视频理解的多模态智能总结系统。项目支持从视频 URL 或本地文件中提取音频与关键帧，使用 LangGraph 编排多智能体工作流，对视频进行图文融合总结，并在总结完成后继续基于同一会话做“时间旅行式”追问。

它不是一个单次输出摘要的脚本，而是一套包含提取层、工作流层、会话持久化层、前端交互层和测试闭环的完整工程化实现。

## 项目亮点

- 多模态总结：同时利用 Whisper 转录文本与视频关键帧进行联合理解。
- 两阶段工作流：第一阶段完成分片规划、结构化上下文提取、波次并行分析与聚合，并强制进入人类审批关口；第二阶段读取同一 checkpoint 完成最终成文与质量审查闭环。
- LangGraph Map-Reduce + Send API：基于 `outline_bootstrap_node` 生成结构化全局上下文，`map_dispatch_node` 按波次（wave）fan-out 音频/视觉 worker，synthesis_barrier 汇聚后再 fan-out 合成 worker，图级原生并行。
- 波次并行容错：每波次按 `WAVE_DISPATCH_SIZE` 派发，worker 接入 timeout / retry / degraded 降级标记，单 chunk 失败不阻塞全局流水线。
- 滑动窗口上下文记忆：调度层按 `CONTEXT_MEMORY_WINDOW_SIZE` 为每个分片注入前 1-2 个相邻压缩摘要，抑制跨分片幻觉漂移。
- Self-RAG 双重防线：先做事实核查（hallucination_grader，接入 structured_global_context 二级约束），再做用户需求对齐（usefulness_grader，接入 aggregated_chunk_insights 证据边界），避免"有文笔但不靠谱"的输出。
- 主动求知工具：当文本或画面出现生僻热词、梗图、未知 UI 或角色时，分析节点可调用 Tavily 搜索补充背景知识。
- 人类在环（HITL）：支持对聚合稿进行人工编辑和补充指导意见，再触发最终总结生成。
- 会话持久化：通过 thread_id + checkpoint 机制保存同一视频分析会话状态。
- 时间旅行追问：总结完成后，用户可以指定某个时间戳继续提问，系统会回溯该时间窗附近的关键帧和语音片段回答。
- 前端会话恢复：Streamlit 页面支持绑定历史 thread_id，并继续对已有会话发起追问。
- 完整测试体系：覆盖工具层、wave 并行、checkpoint 恢复链路、grader 质量闭环与 E2E 烟测。

## 当前能力

### 1. 视频输入

- YouTube URL 下载
- 本地视频上传
- 支持 mp4、mov、avi、mkv 等常见格式

### 2. 提取层

- 音频提取：从视频中分离音频轨
- Whisper 转录：返回 verbose_json，保留时间戳分段
- 场景感知关键帧提取：基于灰度直方图做场景变更检测，降低冗余帧和视觉 Token 浪费

### 3. 工作流层

| 节点 | 职责 |
|------|------|
| `chunk_planner_node` | 按时间轴将 transcript + keyframes 切分为 chunk_plan |
| `outline_bootstrap_node` | 提取结构化全局上下文（entities + timeline_anchors），不生成叙事 summary，供所有 worker 作为一级约束 |
| `map_dispatch_node` | 按 `WAVE_DISPATCH_SIZE` 批量派发当前波次的音频/视觉 worker，并注入滑动窗口压缩摘要 |
| `chunk_audio_worker_node` | 分析当前分片语音证据，输出结构化协议（observation / context_calibration / final_summary） |
| `chunk_vision_worker_node` | 分析当前分片关键帧，输出结构化协议，必要时调用 Tavily 搜索 |
| `synthesis_barrier_node` | 等待当前波次 audio + vision 就绪（含降级态），再 fan-out 合成 worker |
| `chunk_synthesizer_worker_node` | 融合单个分片的音视频洞察，具备 timeout / retry / degraded 容错 |
| `chunk_synthesizer_node` | 收集当前波次合成结果，路由到下一波次或进入聚合 |
| `chunk_aggregator_node` | 按时间顺序拼接所有分片证据，生成 `aggregated_chunk_insights` |
| `human_gate_node` | 强制暂停，第一阶段以 pending 状态结束，等待前端触发人类审批 |
| `fusion_drafter_node` | 基于审批后聚合稿（含 human_guidance）生成全篇 Markdown 总结，Self-RAG 回流重写时携带 feedback_instructions |
| `hallucination_grader_node` | 事实核查（JSON Mode, temp=0），接入 structured_global_context 二级约束，超过 `SELF_RAG_MAX_REVISIONS` 熔断放行 |
| `usefulness_grader_node` | 需求对齐核查（JSON Mode, temp=0），接入 aggregated_chunk_insights 证据边界，防止反馈指令要求凭空补充 |

### 3.1 两阶段工作流

- 阶段一（`build_video_summary_graph`）：提取 → 分片规划 → 结构化全局上下文 → 波次并行分析/合成 → 聚合 → human_gate（结束于 pending 状态），返回 `review_package`
- 阶段二（`build_finalization_graph`）：读取同一 `thread_id` checkpoint，通过人工编辑/指导完成 `fusion_drafter → hallucination_grader → usefulness_grader` 质检闭环，不通过则回流重写（最多 `SELF_RAG_MAX_REVISIONS` 轮）

### 4. 会话与时间旅行

- 为每次工作流运行生成或复用 thread_id
- 使用 checkpoint 保存工作流状态
- 基于 thread_id + timestamp 恢复历史上下文
- 自动提取对应时间窗的语音证据与最近邻关键帧
- 对指定时间点继续发起追问

### 5. 前端交互

- 展示工作流实时状态日志
- 展示当前会话 thread_id
- 绑定已有 thread_id 继续分析或追问
- 在同一页面完成总结与时间旅行问答

## 架构概览

### 处理主链路

```text
视频输入（URL / Upload）
  -> 提取层（下载 / 保存 / 音频提取 / 关键帧提取 / Whisper 转录）
  -> 第一阶段工作流（build_video_summary_graph）
      -> chunk_planner_node（分片规划）
      -> outline_bootstrap_node（结构化全局上下文：entities + timeline_anchors）
      -> map_dispatch_node（按 WAVE_DISPATCH_SIZE 派发当前波次）
           -> [fan-out] chunk_audio_worker_node（结构化协议输出）
           -> [fan-out] chunk_vision_worker_node（结构化协议输出）
           -> synthesis_barrier_node（就绪检测，接受降级态）
           -> [fan-out] chunk_synthesizer_worker_node（timeout/retry/degraded）
           -> chunk_synthesizer_node
               ├─ 波次未完成 → 回到 map_dispatch_node（下一波）
               └─ 全部完成   → chunk_aggregator_node
      -> chunk_aggregator_node（聚合证据底稿）
      -> human_gate_node（强制暂停，第一阶段结束）
  -> review_package（待审批包，含 aggregated_chunk_insights + thread_id）
  -> 人类审批（前端编辑聚合稿 + 填写 human_guidance）
  -> 第二阶段工作流（build_finalization_graph）
      -> fusion_drafter_node（成文，附带 human_guidance + feedback_instructions）
      -> hallucination_grader_node（事实核查，二级证据约束）
           ├─ 有幻觉 → 回到 fusion_drafter_node（重写）
           └─ 无幻觉 → usefulness_grader_node（需求对齐核查，证据边界约束）
               ├─ 不对齐 → 回到 fusion_drafter_node（重写）
               └─ 对齐   → END
  -> 最终 Markdown 总结
```

### 时间旅行链路

```text
已完成的历史会话
  -> thread_id
  -> checkpoint 恢复 channel_values
  -> timestamp 定位最近关键帧 + 语音时间窗
  -> 证据组装
  -> 对该时间点继续提问
```

## 目录结构

```text
video_summarizer/
├── app.py
├── config/
│   ├── __init__.py
│   └── settings.py
├── core/
│   ├── extraction/
│   │   ├── base.py
│   │   ├── infrastructure/
│   │   │   ├── extractor.py
│   │   │   ├── transcriber.py
│   │   │   └── video/
│   │   └── sources/
│   └── workflow/
│       ├── __init__.py
│       ├── api.py
│       ├── checkpoint_factory.py
│       ├── session.py
│       ├── time_travel.py
│       └── video_summary/
│           ├── graph.py
│           ├── state.py
│           ├── edges/
│           ├── nodes/
│           └── tools/
├── services/
│   └── workflow_service.py
├── tests/
│   ├── core/
│   └── integration/
├── docs/
│   ├── analysis/
│   ├── evaluation/
│   │   └── reports/
│   ├── DEV_SPEC.md
│   ├── OBSERVABILITY_RUNBOOK_CN.md
│   └── FRONTEND_BACKEND_API_INTERFACE_CN.md
├── temp/
├── test_output/
├── cookies.txt
├── requirements.txt
└── README.md
```

## 技术栈

- Python
- Streamlit
- LangGraph
- OpenAI API
- Whisper API
- OpenCV
- moviepy
- yt-dlp
- python-dotenv
- tenacity

## 环境要求

- Python 3.10+
- 建议使用虚拟环境
- 能够访问 OpenAI 兼容接口
- 若需要 URL 下载，建议提供 cookies.txt

## 安装

### 1. 克隆项目

```bash
git clone <your-repo-url>
cd video_summarizer
```

### 2. 创建并激活虚拟环境

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS / Linux:

```bash
source .venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

## 配置

在项目根目录创建 .env 文件。

示例：

```ini
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_NAME=gpt-4o
OPENAI_VISION_MODEL_NAME=gpt-4o
TRANSCRIBER_MODEL=whisper-1
TAVILY_API_KEY=tvly-xxxx
OTEL_ENABLED=true
OTEL_EXPORTER=otlp
OTEL_SERVICE_NAME=video-summarizer-backend
OTEL_SAMPLE_RATIO=1.0
OTEL_OTLP_ENDPOINT=http://localhost:4317

# Checkpoint 配置
CHECKPOINT_BACKEND=memory
# CHECKPOINT_DB_URL=postgresql://user:password@host:5432/dbname
```

## 可观测性快速联调

1. 启动 Jaeger：

```powershell
./scripts/start_jaeger_local.ps1
```

2. 启动后端与 Celery Worker（确保加载 .env 中 OTEL 配置）。

3. 触发一条带 tracing 的请求后，执行 smoke 检查：

```powershell
./scripts/check_jaeger_smoke.ps1
```

4. 打开 Jaeger UI：

```text
http://localhost:16686
```

5. 详细排查与运行门禁请参考：

- docs/OBSERVABILITY_RUNBOOK_CN.md

### 环境变量说明

完整变量列表见根目录 `.env_example`，以下为关键分组说明：

**API 与模型**
- `OPENAI_API_KEY`：必填
- `OPENAI_BASE_URL`：兼容中转地址，默认 `https://api.openai.com/v1`
- `OPENAI_MODEL_NAME` / `OPENAI_VISION_MODEL_NAME`：默认均为 `gpt-4o`
- `TRANSCRIBER_MODEL`：默认 `whisper-1`
- `TAVILY_API_KEY`：可选，主动搜索工具

**分片调度与波次**
- `MAP_CHUNK_SECONDS`：每个分片时长，默认 120s
- `WAVE_DISPATCH_SIZE`：每波次派发 chunk 数，默认同 `MAP_MAX_PARALLELISM`
- `CHUNK_WORKER_TIMEOUT_SECONDS`：worker 超时，默认 45s
- `CHUNK_WORKER_MAX_RETRIES`：worker 重试次数，默认 1

**上下文记忆与聚合**
- `CONTEXT_MEMORY_WINDOW_SIZE`：滑动窗口大小（前 N 个 chunk 压缩摘要），默认 2
- `CONTEXT_MEMORY_SUMMARY_MAX_CHARS`：压缩摘要最大字符，默认 160
- `AGGREGATED_CHUNK_INSIGHTS_MAX_CHARS`：聚合稿截断保护，默认 24000

**Self-RAG 质检闭环**
- `SELF_RAG_MAX_REVISIONS`：最大重写轮次（hallucination_grader / usefulness_grader 共用），默认 2

**Outline Bootstrap**
- `OUTLINE_ENTITY_LIMIT_BASE` / `OUTLINE_ENTITY_LIMIT_MAX`：实体数量下/上限，默认 12 / 36
- `OUTLINE_STOPWORDS_DIR`：停用词目录，默认 `{VIDEO_SUMMARY_ROOT}/data/stopwords`

**Checkpoint 与持久化**
- `CHECKPOINT_BACKEND`：默认 memory，预留 postgres 分支
- `CHECKPOINT_DB_URL`：postgres 模式需提供

### 关于 Checkpoint 后端

当前项目默认使用 memory backend，这意味着：

- 同一进程内可用
- 适合本地开发和演示
- 进程重启后会话状态会丢失

代码中已经预留 postgres backend 的适配路径，但当前环境未默认安装对应依赖。如果你要做生产级持久化，需要在部署环境补充该依赖并配置数据库连接串。

## cookies.txt 配置（推荐）

对于 YouTube 下载，建议在项目根目录放置 cookies.txt，以减少风控和登录校验导致的下载失败。

基本步骤：

1. 在浏览器安装导出 cookies.txt 的插件
2. 登录目标视频网站
3. 导出 cookies.txt
4. 放到项目根目录

## 启动方式

```bash
streamlit run app.py
```

默认访问地址：

```text
http://localhost:8501
```

## 前端使用说明

### 1. 生成总结

1. 输入 OpenAI API Key 与 Base URL
2. 选择视频来源：YouTube URL 或 Local Upload
3. 输入总结偏好
4. 点击 Generate Review Draft（生成待审批稿）
5. 在 Human Review 区编辑聚合稿，按需填写 human_guidance
6. 点击 Approve And Generate Final Summary（审批并生成最终总结）
7. 查看最终结果与当前 thread_id

### 2. 绑定历史会话

1. 在侧边栏的 Session 区输入已有 thread_id
2. 点击“绑定为当前会话”
3. 后续追问会自动使用这个 thread_id

### 3. 使用 Time Travel Q&A

1. 在页面下方输入时间戳，例如 00:10 或 00:14:20
2. 选择证据窗口大小
3. 输入追问问题
4. 点击 Ask At Timestamp
5. 查看基于历史证据的回答

## 代码调用示例

### 1. 生成总结

```python
from services.workflow_service import VideoSummaryService

service = VideoSummaryService(api_key="your-key", base_url="https://api.openai.com/v1")

with open("sample.mp4", "rb") as video_file:
    review_package = service.analyze_uploaded_video(
        uploaded_file=video_file,
        original_filename="sample.mp4",
        user_prompt="请重点分析视频中的操作流程",
    )

summary = service.finalize_summary(
    thread_id=review_package["thread_id"],
    edited_aggregated_chunk_insights=review_package.get("editable_aggregated_chunk_insights", ""),
    human_guidance="重点补充架构设计权衡与风险点",
)

print(service.last_thread_id)
print(summary)
```

### 2. 基于历史会话继续追问

```python
from services.workflow_service import VideoSummaryService

service = VideoSummaryService(api_key="your-key")

answer = service.ask_at_timestamp(
    thread_id="existing-thread-id",
    timestamp="00:14:20",
    question="这一段画面中的架构图表达了什么？",
    window_seconds=20,
)

print(answer)
```

## 离线评估系统（轻量版）

本仓库已提供最小可用评估闭环，包含：

- 核心数据集：`evaluation/datasets/core_set.json`
- 批量评估入口：`scripts/run_eval.py`
- baseline 对比工具：`scripts/compare_eval_reports.py`

### 1. 准备数据集

在 `evaluation/datasets/core_set.json` 中维护样本。每条样本至少包含：

1. `sample_id`
2. `source_type`（`url` 或 `local`）
3. `video_source`
4. `user_prompt`
5. `reference_summary` 或 `key_points`

可通过 `enabled` 字段控制是否参与本次批量评估。

### 2. 运行离线批量评估

```bash
python scripts/run_eval.py --dataset evaluation/datasets/core_set.json --output-dir docs/evaluation/reports
```

常用参数：

- `--max-samples 5`：只跑前 5 个启用样本
- `--sample-ids id1,id2`：只跑指定样本
- `--baseline-report docs/evaluation/reports/<baseline_run>/report.json`：生成基础分数对比

运行后将生成：

- `docs/evaluation/reports/<run_id>/report.json`
- `docs/evaluation/reports/<run_id>/report.md`
- `docs/evaluation/reports/<run_id>/samples/<sample_id>/final_summary.md`

### 3. 对比两次评估结果

```bash
python scripts/compare_eval_reports.py \
    --current docs/evaluation/reports/<current_run>/report.json \
    --baseline docs/evaluation/reports/<baseline_run>/report.json
```

默认会在当前报告目录输出：

- `compare_<timestamp>.md`
- `compare_<timestamp>.json`

当前评估默认只关注两个核心指标：

1. Fact（一致性/幻觉风险）
2. Task（任务对齐度）

## 测试

### 快速运行核心测试

```bash
python -m pytest tests/integration/test_api_status_messages.py tests/integration/test_checkpoint_restore_flow.py tests/integration/test_human_review_finalize_flow.py tests/integration/test_workflow_service_session.py tests/core/generation/test_time_travel.py tests/integration/test_time_travel_pipeline.py -q
```

### 运行搜索工具测试

```bash
python -m pytest tests/core/generation/tools/test_search_tools.py -q
```

### 运行 E2E 烟测（默认跳过重型路径）

```bash
set RUN_E2E=false
python -m pytest tests/integration/test_e2e_pipeline.py -q
```

Windows PowerShell:

```powershell
$env:RUN_E2E='false'; python -m pytest tests/integration/test_e2e_pipeline.py -q
```

### 运行真实 E2E

真实 E2E 会触发：

- 文件 I/O
- Whisper 转录
- 多模态工作流
- OpenAI 真实调用

运行前请确认：

- 已配置有效 OPENAI_API_KEY
- tests/data/sample_e2e.mp4 存在
- 愿意承担真实 API 成本

```powershell
$env:RUN_E2E='true'; python -m pytest tests/integration/test_e2e_pipeline.py -q
```

## 当前测试覆盖重点

- 搜索工具：成功、空结果、畸形响应、网络异常
- 两阶段链路：第一阶段待审批包输出、第二阶段审批后 finalize
- 波次调度：`test_map_dispatcher.py`（波次派发、ready-skip、滑动窗口 payload 注入）
- Send API 流程：`test_synthesis_send_api_flow.py`（fan-out/fan-in 完整链路）
- 图级并行：`test_graph_level_parallelism.py`（reducer 合并、并发竞态）
- 结构化 worker 协议：`test_chunk_workers_structured.py`（observation/context_calibration/final_summary、timeout 降级）
- Self-RAG 质检：`test_hallucination_grader.py`（structured_global_context 二级约束、熔断）、`test_usefulness_grader.py`（证据边界、幻觉放大防护）
- 时间旅行：时间戳解析、时间窗抽取、正常路径与降级路径
- Checkpoint：持久化、同一 thread_id 最新状态覆盖、服务层 last_thread_id 复用
- 指标采样：`test_metrics_logging.py`
- E2E 烟测：导入链路与主流程可用性（`RUN_E2E=false` 默认跳过重型路径）

## 已知限制

- 默认 checkpoint backend 为 memory，重启进程后 thread_id 状态会丢失
- postgres 持久化已预留接口，但默认未安装对应依赖
- 第一阶段结束后必须经过人工审批（HITL）才会生成最终总结
- Time Travel 当前基于“最近关键帧 + 时间窗 transcript”做证据恢复，尚未实现 clip-level / frame-level 向量检索
- 前端已支持会话恢复与追问，但尚未实现“历史会话列表管理”

## 未来方向

- 接入 postgres checkpoint，实现跨进程持久化
- 将 Time Travel 从单点时间戳扩展到时间区间问答
- 引入更细粒度的视频知识索引（clip-level / frame-level）
- 将前端 thread_id 管理升级为可浏览、可恢复的历史会话面板

## 许可证

MIT License
