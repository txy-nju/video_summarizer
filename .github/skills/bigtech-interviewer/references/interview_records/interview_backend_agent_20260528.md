# 面试记录 — 后端 Agent 开发工程师 @ 字节跳动

**面试日期**：2026-05-28  
**岗位方向**：后端 Agent 开发工程师（字节跳动）  
**简历摘要**：主导设计多模态视频智能总结系统，覆盖 LangGraph 两阶段多智能体工作流、Redis Streams 领域事件总线、FastAPI 三域微服务架构、Hybrid RAG 检索管线及 Self-RAG 质检闭环。  
**总体印象**：候选人对核心架构设计有清晰的认知，能说清设计动机和实现思路；在量化指标的细节支撑和数据库扩展性方面存在明显知识盲区，部分简历措辞（"幻觉占比 5% 以下"）缺乏可重复验证的方法论支撑。

---

## 详细问答记录

### Q1（方向 A — 系统架构：LangGraph 两阶段工作流）

**问题**：这两个阶段在 LangGraph 里是一个 graph 还是两个独立的 graph？HITL 审批关口具体是怎么实现的？用户在哪个环节介入，介入后 graph 的执行流是怎么继续的？

**候选人原回答**：
> 这两个阶段指的是两个graph审批端口……Phase 1主要是采取翻in翻out的Send API策略，通过将长视频拆分为多个短视频，并将每个短视频利用AI模型进行summary结构化总结，并在最后通过聚合节点生成一篇聚合稿，然后将该聚合稿送到人类审批关口。经过人类审批和指导，将经过人类审批的意见和对应的修改后的聚合稿送入到第二阶段……这里的人类审批端口实现是通过将图进行物理上的分割来实现的……Graph将聚合稿进行最终稿提取，并通过self rag将对最终稿进行有用性和幻觉度审查，如果不通过则进行打回重写，最终实现最终稿的生成。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 部分准确 |
| 关键遗漏 | 未主动说明两个 graph 之间的衔接机制（Phase 1 如何终止、Phase 2 如何被触发）；未提及 Checkpointer 在此架构中的具体作用 |
| 理解偏差 | "物理上的分割"表述模糊，面试官追问后才补充说明非 interrupt 机制；Checkpointer 与"时间旅行追问"的关联未能清楚区分 |

**学习建议**：
- 建议：准备好一句话总结 Phase 1 → Phase 2 衔接路径："Phase 1 graph 执行到 END 节点后，Celery/Workflow Service 将输出写库并将任务状态置为 PENDING_REVIEW；前端人类审批后，调用 API 触发 finalize_graph 以审批结果为 input 启动 Phase 2。"
- 建议：区分 Checkpointer 的两个使用场景：(1) 断点恢复（节点失败重试）；(2) 时间旅行追问（通过 thread_id + checkpoint_id 回溯到特定 graph 状态，支持用户对历史某时刻的结果追问），面试中需分开讲清楚。
- 建议：了解 LangGraph interrupt_before / interrupt_after + Command(resume=...) 机制，能与"物理分图"方案对比说清各自适用场景。

**参考答案**：
本项目采用**物理分图**实现 HITL：Phase 1 (`analyze_graph`) 执行完毕后进入 END 节点，结果持久化到数据库，Celery 任务状态更新为 `PENDING_REVIEW`。用户在前端完成审批提交后，后端 API 接收审批意见，以 `(aggregated_draft, human_feedback)` 为 input 直接构造 Phase 2 (`finalize_graph`) 的初始 State 并调用 `graph.invoke()`，两图无共享 thread_id。

Checkpointer（`AsyncSqliteSaver`）的两个独立用途：  
1. 节点级容错——每个节点执行后存档，Celery Worker 崩溃后可通过 `thread_id` 从最后一个 checkpoint 恢复，无需从头重跑；  
2. 时间旅行追问——QA 检索时携带历史 `checkpoint_id`，LangGraph 回放到该状态下的中间 Summary 列表，支持用户按时间节点精确追问。

---

### Q1-追问1（方向 A — HITL 衔接机制）

**问题**：Phase 1 的 graph 执行完聚合稿之后，它是怎么"停下来等"人类审批的？审批完成后，Phase 2 的 graph 又是由谁、以什么方式触发启动的？

**候选人原回答**：
> Phase1进入end节点，将输出结果直接返还到后端并交由人力高审批。因此它的实现是通过将物理图进行切分，通过在endpoint处获取到对应的聚合稿，并在LangGraph之外对其进行人类审批的。在审批完成之后，由workflow工作流接管进入第二阶段的long graph。它并不是通过LangGraph的interrupt来实现的，是通过代码来固定相应的流程，从而实现对应的人类审批阶段。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 准确 |
| 关键遗漏 | 未说明 Workflow Service 具体通过什么接口或信号触发 Phase 2（API 调用？Celery 新任务？） |
| 理解偏差 | 无 |

**学习建议**：
- 建议：补充"谁来触发 Phase 2"的细节——具体是前端提交审批时调用一个 `POST /videos/{id}/approve` 接口，还是通过 Redis Stream 发布事件再由 Consumer 触发？能说清这一步会显著提升回答完整性。

**参考答案**：
触发链路为：前端用户提交审批意见 → `POST /api/v1/videos/{video_id}/approve`（审批 API）→ Service 层将 `human_feedback` 写入数据库，同时调用 `finalize_workflow_task.delay(video_id, human_feedback)` 创建 Celery 任务 → Celery Worker 拉起 Phase 2 graph 执行。

---

### Q1-追问2（方向 A — Checkpointer 职责）

**问题**：既然 HITL 是在 graph 外部实现的，Checkpointer 在这里具体起什么作用？Phase 1 的 graph 状态是怎么传递给 Phase 2 的——是通过 Checkpointer 恢复，还是直接作为新的 input 传入 Phase 2？

**候选人原回答**：
> Checkpointer的具体作用是用于thread ID的会话恢复机制，防止因为在视频处理模型中，每一个节点都需要由外部API进行调用，常常出现网络错误等情况，为了能够在某个节点出错时进行恢复，防止影响到其他节点。这是LangGraph特有的一个功能，它支持通过某一个checkpoint进行回溯，从而防止在某一个节点失效之后，对所有节点的总结均失效的情况发生。Phase1的graph状态传递到Phase2是将freeze1的输出交由人类审批，之后将人类审批的结果作为输入传入到Phase2。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 部分准确 |
| 关键遗漏 | 未提及"时间旅行追问"场景下 Checkpointer 的使用方式（简历中明确写了这个功能）；未说明 Phase 2 是全新 input 而非从 Phase 1 的 thread_id 中 resume |
| 理解偏差 | 将 Checkpointer 仅理解为"节点级容错"，忽略了其支持历史状态回放的核心价值 |

**学习建议**：
- 建议：研读 LangGraph 文档中 `get_state_history()` API 的用法，理解如何通过 `config={"configurable": {"thread_id": ..., "checkpoint_id": ...}}` 回放到任意历史节点。
- 建议：在简历中写了"时间旅行追问"，面试时必须能说清：用户发出追问请求时，后端如何查找对应 checkpoint_id，如何用它构造 graph 调用参数。

**参考答案**：
Checkpointer 有两个独立职责：  
1. **断点恢复**：每个节点完成后通过 `AsyncSqliteSaver` 将 State 快照存入 SQLite；若某个分片分析节点因 OpenAI API 超时失败，Celery 重试时携带同一 `thread_id` 恢复，已成功节点不重跑；  
2. **时间旅行追问**：用户对视频某片段追问时，后端查询该 `thread_id` 的 checkpoint 历史，找到对应分片的 Summary State，将其注入 RAG 检索上下文，使 QA 结果精确对应到对应时间节点的内容。  
Phase 1 → Phase 2 的状态传递：**直接作为新 input**，Phase 2 的 `thread_id` 与 Phase 1 不同，两图之间无 Checkpointer 共享。

---

### Q2（方向 A — Send API 并发写冲突与自定义 Reducer）

**问题**：多个分片节点并行写回 State 时，冲突是怎么产生的？你的自定义 Reducer 是怎么解决这个冲突的？Reducer 函数的逻辑大概长什么样？

**候选人原回答**：
> 分片写回产生state冲突本质是因为我们利用了LangGraph的Send API机制。Send API将某一个节点作为起点，动态的生成多个路由进行并行分析。当多个节点分析完毕之后，将会尝试向全图的某个具体状态进行写入。如果不使用自定义reducer的话，将会导致不同节点之间的状态相互覆盖，从而导致最终的结果只能看到某一个节点的总结结果，导致其他结果的丢失。我的reducer主要是通过chunk ID进行去重和合并的。因为在长视频进行分片的时候，我会为每一个chunk生成唯一的一个ID，因此ID本身具有去重功能。当reducer执行的时候，会依据chunk ID判断当前是否需要更新还是需要增量。具体的reducer函数是将图状态中的base item和update item进行合并，它首先会对base item进行输入，然后会针对每一个update item通过其chunk ID进行递归深度合并，从而实现将不同chunk ID同时存入到某一个state之中的实现。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 准确 |
| 关键遗漏 | 未提及 Reducer 在 State 定义中的声明方式（`Annotated[list, merge_chunk_reducer]`），也未提到排序问题（合并后 chunks 需要按 `chunk_index` 排序才能正确聚合） |
| 理解偏差 | 无 |

**学习建议**：
- 建议：准备好 Reducer 的代码片段，能快速写出 `Annotated[List[ChunkSummary], merge_chunk_summaries]` 这样的 State 声明。
- 建议：补充说明合并后的 ordering 处理——fan-in 聚合节点拿到 `chunk_summaries` 后需要按 `chunk_index` 排序，才能保证最终稿的时序正确性。

**参考答案**：
```python
def merge_chunk_summaries(
    base: list[ChunkSummary], updates: list[ChunkSummary]
) -> list[ChunkSummary]:
    index = {item.chunk_id: item for item in base}
    for item in updates:
        if item.chunk_id in index:
            # 深度合并：用新字段覆盖旧字段，保留旧字段中 updates 没有的部分
            existing = index[item.chunk_id]
            index[item.chunk_id] = existing.model_copy(update=item.model_dump(exclude_none=True))
        else:
            index[item.chunk_id] = item
    return sorted(index.values(), key=lambda x: x.chunk_index)

class AnalyzeState(TypedDict):
    chunk_summaries: Annotated[list[ChunkSummary], merge_chunk_summaries]
```

---

### Q3（方向 D — Redis Streams 选型）

**问题**：为什么选 Redis Streams，而不是用 Celery 的 apply_async 直接触发任务，或者用 Redis 普通的 Pub/Sub？三者在这个场景下的核心差异是什么？

**候选人原回答**：
> 我用Redis Streams主要是由以下的考量。首先我采用领域驱动设计的思维模式，采用领域事件将上传视频和视频加工两个领域进行解耦，因此我不会直接使用Celery的异步任务，而是采用Redis Stream将生产者和消费者进行彻底的解耦。其次，我采用Redis Stream而不是Redis Pub/Sub，是因为这个任务本身的性质导致的，我们要做的任务是将用户上传后的视频进行总结，这个任务本身相对来说是不容丢失的。而Redis Pub/Sub本身并不能保证消息一定能够被消费到，因此选用Redis Stream，它本身能够支持消息的持久化和消息至少被消费一次。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 准确 |
| 关键遗漏 | 未提及 Consumer Group 的关键机制（XREADGROUP + XACK + PEL 列表 + 失败重投）；未提及 Celery apply_async 的另一个缺点：生产者与消费者代码强耦合（需要 import task 函数），违反域边界 |
| 理解偏差 | 无 |

**学习建议**：
- 建议：补充 Consumer Group 的完整生命周期：`XREADGROUP GROUP g1 consumer1 COUNT 1 STREAMS stream >` 读取 → 处理 → `XACK stream g1 msg_id` 确认；未 ACK 的消息进入 PEL（Pending Entry List），定时 `XPENDING` 扫描后重新投递给其他 consumer，这才是"至少消费一次"的实现机制。
- 建议：面试中提到 Celery apply_async 的域边界问题：上传 Service 要调用 Celery task，就必须 import `process_video_task`，意味着两个域的代码产生直接依赖，违反 DDD 的 Anti-Corruption Layer 原则。

**参考答案**：
三者核心差异：

| 方案 | 消息持久化 | 至少消费一次 | 域边界 |
|------|----------|------------|------|
| Celery apply_async | 依赖 broker（Redis/RabbitMQ） | 支持 | ❌ 生产者需 import task，跨域耦合 |
| Redis Pub/Sub | ❌ fire-and-forget，消费者离线即丢失 | ❌ | ✅ |
| Redis Streams + Consumer Group | ✅ 持久化到 stream log | ✅ PEL + XPENDING 重投 | ✅ |

选 Redis Streams 的核心理由：视频处理任务不可丢失（用户付费场景）+ 需要跨域事件通知 + 本身已有 Redis 基础设施无需引入新组件。

---

### Q4（方向 B — hallucination_guard 评分机制与 5% 指标）

**问题**：hallucination_guard 节点是怎么"评分"的？输入是什么，调用什么模型，输出结构？"5% 以下"这个数字怎么来的？

**候选人原回答（评分机制部分）**：
> hallucination_guard节点的评分具体是以下几个标准：幻觉的严重程度，以及相应的模型权重。它的输入主要包含输入的结构化文档，以及相应的测试集当中的标准答案。它调用的是GPT-4O这个模型，它的规则是针对输入的总结稿进行事实提取，将总结稿拆分为一系列独立事实，并依照标准答案中的事实进行逐一的比对。它会将幻觉分为实体、关系以及凭空编造三个严重程度，分别给予0.2、0.5以及1.0的权重进行相应的打分。它的输出主要包含以下几个部分：评分、幻觉的严重程度、模型本身的自信度，以及其他部分。

**候选人原回答（5% 指标部分）**：
> 5%以下的这个数字主要是通过，主要是我们人工审批了一批视频，并将其并为其制定标准答案，并将数十个视频通过相应的evaluator文件进行评审，并将结果作……（未完成）

**追问（5% 计算口径）**：
> "这个计算口径我也不了解"

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 评分机制：部分准确；5% 指标：存在偏差（无法说明计算口径） |
| 关键遗漏 | 评分机制中未说明"评分低于阈值"的阈值具体是多少，以及如何将各事实的加权分汇总为一个 pass/fail 判断；5% 完全无法解释计算口径 |
| 理解偏差 | "输入包含测试集中的标准答案"——在生产环境中 hallucination_guard 不应依赖外部 ground truth，应基于音视频原始转录文本作为 grounding evidence；将 eval 时的 ground truth 与生产时的 grounding source 混淆了 |

**学习建议**：
- 建议：区分两个场景：(1) **生产时 hallucination check**——输入是"最终稿 + 对应分片的转录文本/关键帧描述"，不依赖人工标注 ground truth，模型判断总结内容能否从原始证据中找到支撑；(2) **离线 eval**——输入是"最终稿 + 人工标注答案"，用于测量系统整体质量。简历描述的是生产时场景，但回答变成了 eval 场景。
- 建议：对简历上的每个量化指标，准备好"测量方法论"一句话：5% 是"以分片转录文本为 ground truth，统计最终稿中被标记为 `FABRICATED`（权重 1.0）的原子事实占总事实数的比例，在 N 个测试视频上的平均值"。
- 建议：阅读 `evaluation/llm_as_a_judge.py` 和 `evaluation/reports/` 中的 eval 报告，确保能讲清 eval 脚本的计算逻辑。

**参考答案**：
生产环境 hallucination_guard 节点：  
- **输入**：`final_draft`（待检稿）+ `grounding_evidence`（对应分片的 Whisper 转录文本 + 关键帧多模态描述）  
- **调用**：GPT-4o，通过结构化 Prompt 要求模型先将 `final_draft` 拆分为原子事实列表，再对每条事实在 `grounding_evidence` 中寻找支撑  
- **输出**：`HallucinationReport(facts: list[FactCheck], weighted_score: float, pass_threshold: float)`，其中每条 `FactCheck` 有 `verdict: Literal["SUPPORTED","ENTITY_ERROR","RELATION_ERROR","FABRICATED"]` 和对应权重  
- **阈值逻辑**：`weighted_score = sum(weight[verdict] for fact in facts) / len(facts)`；score > 0.15 则触发修订，修订上限 2 轮  
- **5% 的测量口径**：在离线 eval 中，统计 `FABRICATED` 类事实占总事实数的比例（最高权重类，代表最严重幻觉），在测试集上该比例 ≤ 5% 则认为系统达标

---

### Q5（方向 F — 系统扩展性压力题）

**问题**：日活上涨 100 倍，并发视频处理量从个位数变成几百个。当前架构最先崩的地方在哪里？水平扩展改造的优先级排序和具体方案？

**候选人原回答**：
> 我认为最先崩的地方应该是Celery Worker，因为当前的Celery Worker主要是采用单服务器部署的方案进行实现的，所以它本身并不能很好的支持高并发的场景。如果让我做水平改造的话，优先级首先是Celery Worker，其次是PostgreSQL，最后是Redis。Celery Worker：因为目前实现的Celery Worker就已经采用了DDD的思想，因此我会将其根据领域的不同进行拆分，如将视频总结和视频提取作为微服务架构放在单独的服务器上。PostgreSQL这一部分的架构扩展，我不是很了解。最后Redis的话，我会考虑采用Redis的主从模式和集群来提高其并发量。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 部分准确 |
| 关键遗漏 | 遗漏了最关键的瓶颈：**OpenAI API 并发限速（TPM/RPM）**——100x 并发下 LLM 调用会被限流，这是视频 AI 处理场景最先触发的瓶颈，比 Celery Worker 本身更致命；遗漏了视频文件存储（本地磁盘 → 对象存储）；遗漏了 Celery 同类 Worker 的**水平复制**（横向扩展）思路 |
| 理解偏差 | 将 Celery 按域拆分说成了"水平扩展"，这实际是**垂直/功能拆分**（Microservice），不等同于水平扩展（多实例同类 Worker 并行消费同一队列）；PostgreSQL 扩展完全不了解 |

**学习建议**：
- 建议：掌握 LLM API 限速应对方案：(1) 多 key 轮转；(2) 指数退避重试（`tenacity`）；(3) 引入优先级队列区分 VIP/普通用户；(4) 与 LLM 供应商协商提高 tier 上限。这是 AI 后端高频考点。
- 建议：理解 Celery Worker 水平扩展 = 在多台机器上启动相同 Queue 的 Worker 进程，Celery Broker（Redis Streams）自动负载均衡分发任务，无需改代码。
- 建议：学习 PostgreSQL 读写分离基本方案：主库处理写操作，只读副本（streaming replication）分担读请求；连接池（PgBouncer）解决高并发连接数问题；热点表（videos、summaries）考虑按 `created_at` 分区。
- 建议：补充视频文件存储瓶颈：本地磁盘无法横向扩展，需迁移到 MinIO/S3，Worker 从对象存储流式读取视频而非本地文件系统。

**参考答案**：
**最先崩的瓶颈排序（从最严重到次要）**：

1. **OpenAI API 限速（RPM/TPM）**——每个视频分片需要 1 次 GPT-4o 调用，100 并发 × 多分片 = 极高 RPM，rate limit 最先触发
2. **Celery Worker 单机计算资源**——CPU/内存不足以维持数百个并发任务
3. **本地视频文件存储**——单机磁盘 I/O 成为瓶颈，无法跨 Worker 共享文件
4. **PostgreSQL 连接数**——高并发下连接池耗尽
5. **Redis 单点**——Stream 写入/读取压力（相对较晚触发）

**水平扩展方案（优先级从高到低）**：

1. **LLM 调用层**：多 API key 轮转 + `tenacity` 指数退避 + 独立限速 Worker 队列
2. **Celery Worker 横向扩展**：在 K8s 上部署多副本相同类型的 Worker，消费同一 Redis Stream 队列，HPA 根据队列积压长度自动扩缩容
3. **视频文件存储**：本地文件系统 → MinIO/S3，Worker 通过 presigned URL 流式读取
4. **PostgreSQL**：主从读写分离 + PgBouncer 连接池；热点查询表考虑按月分区
5. **Redis**：Redis Cluster 模式（一致性哈希分片）+ 主从副本（读请求分流）

---

## 综合建议

1. **量化指标需有方法论支撑**：简历中的"5% 以下"、"60% 提升"等数字，在面试时必须能立即说清"怎么测的、测了多少样本、计算公式是什么"。如果无法解释，建议改为更保守的表述（如"eval 结果显示 hallucination rate 显著下降"）。

2. **区分生产场景与离线 eval 场景**：`hallucination_guard` 在生产时的 grounding source 是转录文本，不是人工标注答案。将两者混淆会让面试官质疑你对自己代码的理解深度。

3. **补充系统扩展性认知**：PostgreSQL 读写分离、连接池、分区表是后端工程师的基础知识盲区，需补课。LLM API 限速应对方案在 AI 后端岗位几乎必考。

4. **LangGraph 机制要说全**：Checkpointer 的时间旅行场景、`interrupt_before` 与物理分图的对比、`get_state_history()` 的使用——这些是区分"用过 LangGraph"和"深度理解 LangGraph"的分界线。

5. **Celery 扩展要区分横向复制与功能拆分**：水平扩展 = 同类 Worker 多实例；功能拆分 = 不同业务 Worker 分队列。前者更直接解决并发问题，后者解决的是隔离性问题。

---

## 下次面试建议重点准备的方向

1. **量化指标的测量方法论**：为简历中每个数字准备"测量脚本 + 样本规模 + 计算公式"三件套，阅读 `evaluation/` 目录下的评估报告，能完整讲清 eval pipeline。

2. **数据库扩展性（PostgreSQL）**：重点学习读写分离、PgBouncer 连接池、表分区、慢查询分析（EXPLAIN ANALYZE），这是后端岗位高频考点且本次完全未作答。

3. **LangGraph 深层机制**：`interrupt_before/after`、`Command(resume=...)`、`get_state_history()`、Checkpointer 时间旅行 API 的完整用法，确保能与自己项目实现对应讲解。
