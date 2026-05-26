# 项目系统架构速览

本仓库当前主链路是一个围绕视频资源、异步处理、总结工作流和问答检索构建的多模态 AI 后端系统。

## 核心分层

1. 接入层：FastAPI 路由与 WebSocket 负责请求接入、鉴权、长连接进度推送。
2. 服务层：组织业务编排、任务状态流转、进度发布、RAG 与工作流协调。
3. 异步任务层：Celery 承载视频转录、抽帧、向量化与 LangGraph 工作流运行。
4. 持久化层：SQLAlchemy ORM、Pydantic Schema、Repository 收口数据访问。
5. 核心引擎层：`core/extraction` 负责多模态提取，`core/workflow` 负责总结图编排和时间旅行问答。

## 适合拆成学习考点的方向

1. 系统整体架构与模块职责
2. 视频上传后的异步任务编排
3. LangGraph 工作流设计与状态流转
4. 多模态数据提取与融合
5. RAG / Time Travel 问答机制
6. 可观测性、状态上报与故障处理
7. Repository、Schema、Service 的分层边界

## 典型学习问法

1. 一个视频从上传到总结完成，跨过了哪些模块和状态节点？
2. 为什么把耗时逻辑放进 Celery，而不是直接在 HTTP 请求里完成？
3. LangGraph 图中的节点、路由和 checkpoint 分别承担什么责任？
4. 多模态链路里 transcript、keyframes、向量化结果最终如何被消费？
5. 如果用户回答含糊，优先追问模块职责、数据流、异常处理和工程取舍。