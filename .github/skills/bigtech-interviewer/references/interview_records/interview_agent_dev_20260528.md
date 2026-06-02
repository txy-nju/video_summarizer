# 面试记录 — Agent 开发工程师

**面试日期**：2026-05-28
**岗位方向**：Agent 开发工程师
**简历摘要**：主导设计多模态视频智能总结系统，覆盖 LangGraph 两阶段多智能体工作流、Hybrid RAG 检索管线（稠密+稀疏双路召回 + Reranker + KeyframeLookup）、Self-RAG 双重质检闭环、FastAPI 三域微服务架构及 OTel 全链路追踪。
**总体印象**：候选人对核心 AI 链路（多模态 Prompt 构造、Self-RAG 循环机制、RAG 检索流程）有清晰的实现认知，能说清设计动机；OTel 可观测性方向完全空白，RAG 检索组件存在关键事实偏差（Reranker 类型、是否使用 RRF），向量维度数字有误。

---

## 详细问答记录

### Q1（方向 B — 多模态 Prompt 设计）

**问题**：`chunk_multimodal_analyzer` 节点处理每个视频分片时，它的输入是怎么组织的？关键帧图片和 Whisper 转录文本是通过什么方式拼成 multimodal message 发给 GPT-4o 的？有没有对 Prompt 长度或图片数量做限制，为什么？

**候选人原回答**：
> 多模态节点的输入主要通过以下几个方式进行组织。一首先是全局的结构化总结。第二部分是如果当前节点处理的chunk处于中间态，那么它会接受来自前文chunk的chunk summary作为参考。第三部分是user prompt，第四部分是对应的file PS，frame PS及图片路径。第5部分是对应的枪枪对应模板的对应chunk的transcript。将这五部分作为输入进行输出。关键帧图片和whisper转录文本主要是通过时间戳这一关键信息进行总结的。Whisper转录文本会对每一句话带有相应的时间戳总结。关键帧图片在截取时也会进行时间戳定位。因此两者可以通过这一共同信息进行关联。针对每一个chunk将chunk时间戳范围内的图片和文字进行检索，然后共同发给多模态模型。我们的chunk本身视频长度大约在5~8分钟以内，在一般情况下密度信息不会超过5000 token，在大模型多模态大模型能够接受的范围内。因此，我们并没有对图片数量做过多的限制。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 部分准确 |
| 关键遗漏 | 未说明图片是以 base64 编码格式传入（不是"图片路径"），也未提及 `detail="low"` 参数；max_tokens 实际为 2000 而非 5000 |
| 理解偏差 | "图片路径"的表述容易被理解为传的是本地文件路径，实际实现是 base64 编码后拼入 `image_url` 的 data URI；"5000 token 以内"与实际 max_tokens=2000 配置不符 |

**学习建议**：
- 建议：掌握 OpenAI multimodal message 的具体格式：`{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,{b64}", "detail": "low"}}`，并能说清 `detail="low"` 与 `detail="high"` 的 token 消耗差异（low≈85 tokens/图，high≈最多 1105 tokens/图）。
- 建议：核对代码中的实际 `max_tokens` 配置（`chunk_multimodal_analyzer.py` 中为 2000），面试中不要说"不会超过 5000"，数字要来自代码，不要凭感觉估算。
- 建议：补充说明为什么选 `detail="low"`：教学视频关键帧的主要信息是文字板书、图表，低分辨率下依然可读，且成本仅为 high 模式的 1/13。

**参考答案**：
每个 chunk 的 multimodal message 构造分三层：

1. **System Prompt**：包含全局视频结构化信息（标题、总时长、章节列表）和任务指令；
2. **上下文 User Message**：若当前 chunk 非首个分片，将前一个 chunk 的 `ChunkSummary` 序列化后作为"前文摘要"注入，避免割裂叙述；
3. **多模态 User Message**：交替插入关键帧（base64 data URI，`detail="low"`）和对应时间段的 Whisper 转录文本，格式为：
   ```python
   [
     {"type": "text", "text": "关键帧时间戳: 00:01:23"},
     {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,...", "detail": "low"}},
     {"type": "text", "text": "[转录文本段落]"},
     ...
   ]
   ```

**图片数量控制**：最多均匀采样 **15 张**关键帧，超出时均匀下采样；`max_tokens=2000`（代码中硬编码）；`detail="low"` 下每张约 85 tokens，15 张 ≈ 1275 tokens，加上转录文本通常在 2000 token 以内。

---

### Q1-追问1（方向 B — 图片数量与 token 开销）

**问题**：你刚才说"并没有对图片数量做过多限制"——一个 5~8 分钟的 chunk，如果按每 N 秒一帧来抽取，实际会有多少张关键帧？这些图片是以 base64 还是 `image_url` 方式传入 GPT-4o？每张图片在 token 消耗上是什么量级，你算过一个 chunk 的图片 token 开销吗？

**候选人原回答**：
> 我们的抽取逻辑是按照视频长度来进行的。如果说是短视频的话，我们会抽取，会在每秒内进行较多图片的抽取。如果是长视频的话，抽取频率会相应的进行降低。而我们抽取也是讲究一定的逻辑的，即只有当画面进行剧烈抖动，画面灰度、直方差距过大的时候，才会对图片进行截取。实际算下来，一个正常视频7~8分钟大约有75张图片。因此我们实际上在项目当中是会对其进行相应的截取的。我们大约会以截取包含约15张图片作为均匀的拆分出15张图片，并将其作为证据发送给大模型。每一个chunk的token开销大约是在2500左右。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 准确 |
| 关键遗漏 | 未说明图片传入格式（base64）和 `detail` 参数；2500 token 与实际 max_tokens=2000 略有出入，但数量级合理 |
| 理解偏差 | 无 |

**学习建议**：
- 建议：补充"75 帧中均匀采 15"的信息损失权衡——为什么不用场景检测结果直接选 15 帧而是从 75 帧中均匀采样？能说出"均匀采样保证时序分布，场景检测解决初始截帧稀疏的问题"会更完整。

**参考答案**：
场景变化检测（灰度直方图差异阈值）先将 7-8 分钟视频截出约 75 帧候选，再从中**均匀采样最多 15 张**传入模型（代码中 `MAX_FRAMES_PER_CHUNK=15`）。图片以 `data:image/jpeg;base64` 格式传入 `image_url.url` 字段，`detail="low"`，每张约 85 tokens，15 张约 1275 tokens。`max_tokens=2000`（代码配置）。

---

### Q2（方向 C — Self-RAG 修订循环）

**问题**：简历里写了"hallucination_guard 评分低于阈值自动触发修订，最多 2 轮"。这 2 轮修订在 graph 里是怎么实现的——触发重写之后，重写节点的输入是什么？是把整篇最终稿全部重写，还是只改有问题的片段？第二轮修订时，它怎么知道第一轮改了哪里、还有什么没改好？

**候选人原回答**：
> 这两轮修订在graph当中是通过循环图、循环边进行修订的。从hallucination guard评分结束后，会针对评分进行相应的评估，判断其图的流向是流向下一个usefulness节点，还是说回到finalize draft node进行重新重写。因此重写的输入是finalize draft node本身。如果是重写的，重写部分是将所有的最终稿和对应的hallucination guard的相应幻觉评价，全部发送给finalize draft node进行重写。在第二轮修订时，它仍然依靠hallucination进行幻觉定位和重写。因为其本身保障了第一轮重写是完成了的。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 准确 |
| 关键遗漏 | 未说明 revision_count 计数器机制（主动在追问中才说出）；未提及整稿重写的 token 成本代价 |
| 理解偏差 | 无 |

**学习建议**：
- 建议：主动说出整稿重写的成本权衡——"将整稿 + 幻觉报告全部传入 finalize_draft 重写，而非只改有问题的片段，是以确定性换了成本：片段级修订需要准确定位边界并做局部替换，容易破坏上下文连贯性，而整稿重写在 token 成本可接受的前提下（最多 2 轮）更稳定。"
- 建议：补充说明 `fusion_drafter`（finalize_draft 节点）重写时的 Prompt 组织：输入 = `(aggregated_draft, human_feedback, hallucination_report_from_guard)`，让 LLM 有针对性地修改而不是从零生成。

**参考答案**：
Phase 2 `finalize_graph` 中的循环通过**条件边**实现：`hallucination_grader` 节点评分后，路由函数检查 `score == "yes"（存在幻觉）AND revision_count < MAX_REVISIONS(2)` → 回到 `fusion_drafter` 节点重写；否则 → 进入 `usefulness_grader`。重写输入为完整最终稿 + 本轮 `HallucinationReport`（包含每条有问题事实的定位和幻觉类型），整稿重写而非片段修复。代码中关键逻辑：
```python
if not draft or revision_count >= settings.MAX_REVISIONS:
    return {"hallucination_score": "no", "feedback_instructions": ""}
```
当 `revision_count >= 2` 时，`hallucination_grader` 强制返回 `"no"`，跳过幻觉判定，直接放行到 `usefulness_grader`。

---

### Q2-追问1（方向 C — 修订计数器机制）

**问题**：你刚才说"最多 2 轮"——这个上限是怎么强制执行的？如果两轮之后 hallucination_guard 评分依然不合格，graph 会怎么处理？`revision_count` 这个计数器存在哪里，谁来递增它，退出条件的逻辑长什么样？

**候选人原回答**：
> 我们会通过图的状态来判断，全图字段中有相应的重写轮数字段。当hallucination节点以及usefulness节点进行条件判断时，会参考当前全图的retry字段判断是否已经到达上限，如果到达上限，则直接放行，不会再进行相应的重写。revision count这个计数器存在于全图state状态当中，它的递增是依靠hallucination节点在判断需要打回重写的时候进行递增的。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 准确 |
| 关键遗漏 | 将字段名说成"retry"，实际代码中字段名为 `revision_count`；未说明达到上限时 hallucination_grader 内部会强制返回"通过"而不是靠条件边判断 |
| 理解偏差 | 无重大偏差，字段命名记忆有轻微误差 |

**学习建议**：
- 建议：记住代码中的字段名是 `revision_count`（而非 `retry`），面试时引用代码中的准确命名能体现你真正读过自己的代码。
- 建议：补充达到上限时的处理细节：`revision_count >= MAX_REVISIONS` 时，`hallucination_grader` 直接 **在节点内部** 返回 `hallucination_score="no"`，而不是依赖条件边外部判断——这是一个细微但重要的设计点，说明 guard 节点本身负责上限逻辑。

**参考答案**：
`revision_count` 字段存在于 `FinalizeState` 中；`hallucination_grader` 节点在评分开始前首先检查：
```python
if not draft or revision_count >= settings.MAX_REVISIONS:
    return {"hallucination_score": "no", "feedback_instructions": ""}
```
——直接短路返回"通过"，无论实际幻觉评分如何；计数器在 `fusion_drafter` 节点重写完成后由 State 更新机制递增（`revision_count + 1`）。`MAX_REVISIONS=2` 在 `config/settings.py` 中配置。

---

### Q3（方向 C — Hybrid RAG 检索策略）

**问题**：简历写了"稠密+稀疏双路召回+Reranker 精排+KeyframeLookup 时间戳关键帧匹配"。双路召回的结果怎么合并？用的是什么融合算法，权重是怎么定的？Reranker 用的什么模型，它的输入是什么格式？最终送给 LLM 的 context 是多少个 chunk，有没有 token 长度限制？

**候选人原回答**：
> 双路召回的结果，我采用的是RRF算法进行合并。其本身并不包含权重，而是采用排名的倒数进行打分。具体来说，会对所有检索的片段的排名倒数加K之后，进行倒数求和，从而获取到该chunk对应的分数。Rerank用的是crossencoder模型，其输入包含user prompt和对应的检索到的向量文本。最终送给大模型的context chunk由top k参数决定。Top k参数，其本身是不包含长度限制的，因为我们在chunk切分过程当中就已经对相应的文本长度做出了限制。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 存在偏差 |
| 关键遗漏 | top_k 的实际值（6）未说出；未提向量数据库名称（追问后才补充） |
| 理解偏差 | **Reranker 类型错误**：代码中 Reranker 是 **LLM-based**（`provider="llm"`），不是 CrossEncoder 模型；**RRF 在代码中未找到**，实际是直接 top_k 检索 + LLM rerank，没有 RRF 融合层 |

**学习建议**：
- 建议：核对代码 `rag_settings_factory.py` 中的 Reranker 配置：`provider="llm"`，即用 LLM（GPT-4o）对召回结果按相关性重排序，不是 CrossEncoder 模型（如 `cross-encoder/ms-marco-MiniLM-L-6-v2`）。LLM Reranker 的优点是无需单独部署模型、对语义理解更强；缺点是延迟高、成本高。
- 建议：如果你写了"RRF"在简历或面试中，需要确认代码中是否真的实现了 RRF。若没有，改为描述实际实现："稠密检索 top_k=6 + LLM 精排"，不要使用与实现不符的算法名称。
- 建议：补充 top_k=6 这个具体数字，以及选 6 的理由（LLM context 窗口限制下的经验值，过多噪声增加、过少召回率下降）。

**参考答案**：
当前代码实现（`rag_settings_factory.py`）：
- **稠密召回**：ChromaDB 向量检索，`text-embedding-3-small`，top_k=6
- **稀疏召回**：BM25 关键词检索，top_k=6
- **合并**：无 RRF，两路各取 top_k 后合并去重，送入 Reranker
- **Reranker**：`provider="llm"`，即调用 LLM 对候选 chunks 按 query 相关性打分重排，输入为 `(query, [chunk_text_1, ..., chunk_text_N])`
- **最终 context**：Reranker 排序后取前 top_k=6 个 chunks 拼入 Prompt

如需升级为真正的 RRF，需在两路结果各自保留排名，按 `score = ∑ 1/(k + rank_i)` 计算融合分，k=60 是常用默认值。

---

### Q3-追问1（方向 C — 向量数据库与 embedding 模型）

**问题**：稠密召回那路，向量存在哪里？用的是什么向量数据库，还是直接用 PostgreSQL 的 pgvector 扩展？embedding 是哪个模型生成的，向量维度是多少？

**候选人原回答**：
> 稠密召回内录的向量存在chroma DB当中。我们选用向量数据库考虑到的是，向量数据库天然对高维向量检索迅速，而且chroma DB本身作为轻量的向量数据库，适合在项目初期作为最小MVP的实践方案。Embedding模型是通过OpenAI的text-embedding-3-small生成的，其向量维度是七百五十六。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 部分准确 |
| 关键遗漏 | 无 |
| 理解偏差 | **向量维度错误**：`text-embedding-3-small` 默认维度为 **1536**，可通过 `dimensions` 参数自定义缩减（最小 512）；756 不是该模型的标准维度，可能是记忆有误 |

**学习建议**：
- 建议：查阅 OpenAI Embeddings 文档，记住 `text-embedding-3-small` 默认维度 **1536**；若项目中有自定义 `dimensions` 参数配置，说明具体值及选择理由（如降维至 512 节省存储和检索开销）。
- 建议：面试中涉及具体数字时，先说"默认是 1536，我们项目中的实际配置需要核查代码"，不要自信报一个不确定的数字。

**参考答案**：
ChromaDB（`data/db/chroma`，本地持久化），embedding 模型 `text-embedding-3-small`（可通过环境变量 `RAG_EMBEDDING_MODEL` 配置）。该模型默认维度 **1536**；若通过 `dimensions` 参数自定义缩减，常用选项为 512 或 768。向量隔离通过 ChromaDB metadata 中的 `collection` 字段实现，而非多 collection。

---

### Q4（方向 D — OTel 全链路追踪）

**问题**：简历写了"OTel 全链路追踪"。具体问：LLM 调用（GPT-4o）是怎么被 trace 的？一次视频处理请求在 Jaeger 里会看到哪些 span？有没有对 token 用量、Prompt 内容做记录？如果某个分片分析节点耗时异常，你在 Jaeger 里怎么定位到是哪个分片出了问题？

**候选人原回答**：
> 这一部分我不了解。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 未作答 |
| 关键遗漏 | OTel 集成方式、span 结构、chunk_id 作为 scope_id 定位分片、token 不记录（安全考量）等全部缺失 |
| 理解偏差 | — |

**学习建议**：
- 建议：阅读 `backend/observability/` 目录，重点看 `llm_tracing.py` 中的 `trace_llm_call()` 上下文管理器；理解 span 的 `scope_id` 字段如何携带 `chunk_id`，这是在 Jaeger 中定位异常分片的关键。
- 建议：掌握 OTel 的核心概念：Tracer → Span → SpanContext；理解 span 的 parent-child 关系如何将 API 请求 → Celery Task → LangGraph Node → LLM Call 串联成一棵 Trace 树。
- 建议：补充一个重要设计决策：Prompt 内容**不记录**到 span attributes（防止含 PII 的用户内容泄漏到监控系统），只记录 `model`、`scope`、`scope_id`、`retry_count`、`error_code` 等元信息。

**参考答案**：
项目通过 `trace_llm_call()` 上下文管理器（`backend/observability/llm_tracing.py`）为每次 LLM 调用创建 OTel Span，span name 格式为 `{domain}.{stage}.{action}`，如 `workflow.chunk_multimodal.analyze`。

关键 span attributes：
- `model`：`gpt-4o`
- `scope`：如 `chunk_multimodal_worker`
- `scope_id`：**`chunk_id`**（这是定位异常分片的关键字段）
- `workflow_state`：如 `ANALYSIS`、`FINAL_GENERATING`
- `retry_count`、`error_code`

在 Jaeger 中定位异常分片：搜索 `scope_id = "chunk_{n}"` 即可过滤出该分片所有 LLM 调用的 span，通过时长排序快速定位慢调用。**Prompt 内容和 token 用量不记录到 span**（安全策略，防止用户内容进入监控系统）。

---

### Q5（方向 E — cited_sources 引用溯源）

**问题**：简历写了"跨知识库全局 QA 支持 cited_sources 引用溯源"。用户问了一个跨多个视频的问题，LLM 返回的答案里 `cited_sources` 是怎么生成的？检索召回之后，系统是如何知道每段回答来自哪个视频、哪个时间段的？这个溯源信息是在 LLM 生成时传入 Prompt，还是生成完再做后处理匹配？

**候选人原回答**：
> 当用户询问跨视频检索问题的时候，我们会采取自研的RAG系统进行检索。其返回答案里面的cited sources，本身就是来自我们RAG系统入库时存入chroma DB里面的metadata，其生成逻辑在于用户上传视频后，会为每一个视频分配唯一的video ID存入metadata中。而当对应的chunk被检索到后，我们可以通过chunk中的metadata索引到对应的视频，从而形成相应的cited sources。检索系统召回之后，正是通过metadata中的video ID以及相应的transcript time，获取到对应文本的相关信息。溯源信息是在LLM生成时传入prompt的。因为其检索机制本身检索的是chunk块儿，因此我们会在检索到相应的chunk块儿之后，它自带相应的metadata，其中便包含了对应的视频和时间戳信息。

**评估**：

| 维度 | 说明 |
|------|------|
| 理解准确性 | 准确 |
| 关键遗漏 | 未说明 cited_sources 具体返回哪些字段（video_id、video_title、timestamp_range 等）；未说明 LLM 是否被要求在生成文本中标注引用编号（如 [1][2]）还是仅在响应元数据中附带 |
| 理解偏差 | 无 |

**学习建议**：
- 建议：准备好 `cited_sources` 的具体数据结构：`[{"video_id": "...", "video_title": "...", "chunk_index": n, "start_time": "00:01:23", "end_time": "00:03:45"}]`，能在面试时给出具体字段名会大幅提升回答的说服力。
- 建议：补充两种 cited_sources 实现方式的区别：(1) **随 context 携带元信息**——召回的 chunk 本身带 metadata，生成完成后直接将参与构建 context 的所有 chunk 的 metadata 作为 cited_sources 返回，LLM 不需要在文本中标注引用；(2) **LLM 内联引用**——Prompt 中要求 LLM 在答案中写 `[来源1]`，再做后处理映射。能说出项目选的是哪种以及理由。

**参考答案**：
`cited_sources` 的生成流程：

1. **入库时**：每个 chunk 的 ChromaDB document 附带 metadata：`{"video_id": "...", "video_title": "...", "chunk_index": n, "start_time": "...", "end_time": "..."}`；
2. **检索时**：RAG 管线召回 top_k chunks，每个 chunk 的 document 对象包含完整 metadata；
3. **返回时**：不要求 LLM 在生成文本中标注引用编号；而是将所有参与构建 context 的 chunk 的 metadata 收集为 `cited_sources` 列表，随 LLM 生成的答案文本一起返回给前端；
4. **前端展示**：将 `cited_sources` 渲染为可点击的视频时间戳链接，供用户跳转到对应片段复盘。

---

## 综合建议

1. **区分"简历中写了什么"和"代码里实现了什么"**：本场面试中，Reranker 类型（CrossEncoder vs LLM）和召回合并算法（RRF vs 直接合并）均与代码实现不符。建议在面试前通过 `grep` 或 IDE 搜索确认关键技术组件的实际实现，不要依靠记忆。

2. **OTel 可观测性是 Agent 开发岗高频考点**：本场完全未作答。需补课 OTel 核心概念（Tracer/Span/Context）、项目中 `trace_llm_call()` 的使用方式，以及如何通过 `scope_id=chunk_id` 在 Jaeger 中定位异常分片。

3. **量化数字要来自代码而非估算**：embedding 维度（756 vs 1536）、max_tokens（5000 vs 2000 配置）等数字说错会让面试官质疑候选人对自己代码的掌握程度。

4. **RAG 架构叙述需精准**：知道"有 Reranker"不够，需能说清 Reranker 的类型、输入格式、与 CrossEncoder 方案的对比取舍，以及 top_k 的具体值和选取理由。

5. **Self-RAG 和 Prompt 设计掌握较好**：这两块是本场的强项，条件边 + revision_count 的循环机制、15 帧均匀采样 + base64 的构造逻辑基本清晰，建议进一步练习用更简洁的语言在 1-2 分钟内说清楚。

---

## 下次面试建议准备的方向

1. **OTel 全链路追踪**：重点阅读 `backend/observability/llm_tracing.py`，掌握 span name 格式、`scope_id` 与 `chunk_id` 的对应关系、token 不记录的安全理由，以及在 Jaeger 中排查慢 chunk 的实际操作流程。

2. **RAG 组件细节对齐**：核查 `backend/infrastructure/rag_settings_factory.py`，明确 Reranker 实现（LLM-based）、top_k 值（6）、是否有 RRF，然后重新组织 RAG 架构的标准表述，确保每个技术词都与代码一一对应。

3. **embedding 模型参数**：查阅 `text-embedding-3-small` 官方文档，记住默认维度 1536，了解 `dimensions` 参数的使用方法和降维对检索质量的影响，以及与 `text-embedding-3-large`（3072 维）的适用场景差异。
