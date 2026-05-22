# 可观测性运行手册（Jaeger / OTEL）

## 1. 目标

最小联调闭环：
- 后端收到 HTTP 请求
- 异步任务进入 Celery
- Workflow 与 LLM 节点执行
- 在 Jaeger 中可检索到同一条 trace

## 2. 必要配置

在 .env 中设置：

```ini
OTEL_ENABLED=true
OTEL_EXPORTER=otlp
OTEL_SERVICE_NAME=video-summarizer-backend
OTEL_SAMPLE_RATIO=1.0
OTEL_OTLP_ENDPOINT=http://localhost:4317
OTEL_JAEGER_ENDPOINT=http://localhost:4317
```

说明：
- OTEL_EXPORTER 推荐 otlp。
- OTEL_SAMPLE_RATIO 建议联调时设为 1.0，生产可降为 0.1 或更低。
- OTEL_SAMPLE_RATIO 必须在 [0,1]。

## 3. 本地启动顺序

1. 启动 Jaeger

```powershell
./scripts/start_jaeger_local.ps1
```

2. 启动后端 API

```powershell
uvicorn backend.main:app --reload
```

3. 启动 Celery Worker

```powershell
celery -A backend.tasks.celery_app worker -l info
```

4. 验证 Jaeger 可访问且 service/span 已可检索

```powershell
./scripts/check_jaeger_smoke.ps1
```

可选参数示例：

```powershell
./scripts/check_jaeger_smoke.ps1 -Lookback 2h -RequiredOperations http.request.handle,celery.task.start,llm.inference.generate
```

## 4. 验证步骤

1. 发起一条会触发异步链路的请求（如上传并触发处理、或任务工作流接口）。
2. 运行 smoke 脚本，确认 Jaeger 已索引到目标 service 与关键 operation：

```powershell
./scripts/check_jaeger_smoke.ps1
```

3. 打开 Jaeger UI：
- http://localhost:16686
4. 选择 service：
- video-summarizer-backend
5. 搜索最近 trace，确认至少包含：
- http.request.handle
- celery.task.start / finish
- workflow 关键节点 span
- llm.inference.generate

## 5. 常见故障

1. Jaeger UI 无数据
- 检查 OTEL_ENABLED 是否为 true
- 检查 OTEL_OTLP_ENDPOINT 是否可达
- 检查 worker 是否重启后加载了新环境变量
- 可先执行 `./scripts/check_jaeger_smoke.ps1` 区分“Jaeger 不可达”和“有 service 但无 trace”

2. 只有 HTTP span，没有 Celery span
- 检查任务入参是否透传 trace_id
- 检查 task hooks 是否已注册

3. 只有 Workflow span，没有 LLM span
- 检查节点内是否使用了 trace_llm_call 包装

4. 采样导致“偶现看不到”
- 联调期间将 OTEL_SAMPLE_RATIO 调为 1.0
