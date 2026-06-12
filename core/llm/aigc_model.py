from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List

import requests as _requests
from openai import OpenAI

from core.llm.base import BaseModel
from core.llm.transcription_result import TranscriptionResult


# ── AIGC lasr 长语音转写配置辅助函数 ────────────────────────────────────

def _get_aigc_lasr_config(key: str, default: str) -> str:
    """从环境变量读取 AIGC lasr 公共参数，带默认值。"""
    return os.getenv(key, default).strip()


def _get_aigc_user_id() -> str:
    """生成或读取 AIGC user_id（32 位小写字母+数字）。"""
    from_env = _get_aigc_lasr_config("AIGC_USER_ID", "")
    if from_env:
        return from_env[:32]
    # 自动生成：基于 uuid4 hex（32 位）
    return uuid.uuid4().hex


def _get_aigc_package() -> str:
    return _get_aigc_lasr_config("AIGC_PACKAGE", "video_summarizer")


def _get_aigc_client_version() -> str:
    return _get_aigc_lasr_config("AIGC_CLIENT_VERSION", "1.0.0")


# ── AigcModel ────────────────────────────────────────────────────────────

class AigcModel(BaseModel):
    """vivo AIGC provider implementation (OpenAI-compatible API).

    vivo 蓝心大模型通过 OpenAI 兼容接口暴露 chat / vision 能力，
    可直接使用 openai SDK 客户端调用。

    支持的模型：
    - 纯文本（chat）：Volc-DeepSeek-V3.2
    - 多模态（vision）：Doubao-Seed-2.0-mini / lite / pro、qwen3.5-plus
    - 语音转写（transcribe）：vivo 长语音转写 REST API（/lasr/*），
      5 阶段流程，5MB 固定分片，支持说话人分离

    注意：
    - 每个 client 实例初始化时生成 requestId UUID 作为 default_query，
      与官方文档示例完全一致。
    """

    supports_transcribe = True
    max_audio_upload_bytes = 500 * 1024 * 1024  # 最大 500MB
    audio_chunk_size_bytes = 5 * 1024 * 1024  # 固定 5MB 分片上传

    DEFAULT_BASE_URL = "https://api-ai.vivo.com.cn/v1"
    LASR_BASE_URL = "https://api-ai.vivo.com.cn"

    # lasr 轮询配置
    LASR_POLL_INTERVALS = (1.0, 2.0, 3.0, 5.0)  # 指数退避
    LASR_POLL_MAX_SECONDS = 600  # 最大轮询 10 分钟
    LASR_SLICE_SIZE = 5 * 1024 * 1024  # 5MB

    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise ValueError(
                "Missing API key for AIGC provider. "
                "请设置 AIGC_API_KEY 或 CHAT_API_KEY 环境变量。"
            )
        self._api_key = api_key  # 保存原始 key 供 lasr API 使用
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url or self.DEFAULT_BASE_URL,
            default_headers={
                "Content-Type": "application/json; charset=utf-8",
            },
            default_query={"request_id": str(uuid.uuid4())},
        )

    # ── BaseModel 接口实现 ─────────────────────────────────────────────

    def chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        response_format: Dict[str, str] | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            kwargs["response_format"] = response_format
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if timeout is not None:
            kwargs["timeout"] = timeout

        response = self._client.chat.completions.create(**kwargs)
        if not response.choices:
            return ""
        return response.choices[0].message.content or ""

    def stream_chat_completion(
        self,
        *,
        model: str,
        messages: List[Dict[str, Any]],
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        with self._client.chat.completions.create(**kwargs) as stream:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta

    def transcribe_audio(
        self,
        *,
        model: str,
        audio_path: Path,
        response_format: str = "verbose_json",
    ) -> TranscriptionResult:
        """vivo 长语音转写 5 阶段流程。

        model 参数在此 provider 中用作 engineid（默认 "fileasrrecorder"）。
        response_format 参数忽略（vivo API 固定返回格式）。
        """
        engine_id = model or "fileasrrecorder"
        session_id = uuid.uuid4().hex

        print(f"[AIGC] 开始长语音转写: {audio_path.name} (engine={engine_id})")

        # Phase 1: 创建音频
        audio_id = self._lasr_create(audio_path, session_id, engine_id)
        print(f"[AIGC] Phase 1/5 create 完成: audio_id={audio_id}")

        # Phase 2: 分片上传
        self._lasr_upload(audio_path, audio_id, session_id, engine_id)
        print(f"[AIGC] Phase 2/5 upload 完成")

        # Phase 3: 创建任务并开始转写
        task_id = self._lasr_run(audio_id, session_id, engine_id)
        print(f"[AIGC] Phase 3/5 run 完成: task_id={task_id}")

        # Phase 4: 轮询进度
        self._lasr_poll_progress(task_id, session_id, engine_id)
        print(f"[AIGC] Phase 4/5 progress 完成")

        # Phase 5: 获取结果
        result_data = self._lasr_get_result(task_id, session_id, engine_id)
        print(f"[AIGC] Phase 5/5 result 完成")

        return TranscriptionResult.from_aigc_lasr_response(result_data)

    # ── lasr 5 阶段内部方法 ──────────────────────────────────────────

    def _build_lasr_public_params(self, engine_id: str) -> Dict[str, str]:
        """构建 lasr API 公共 URL 参数。"""
        return {
            "client_version": _get_aigc_client_version(),
            "package": _get_aigc_package(),
            "user_id": _get_aigc_user_id(),
            "system_time": str(int(time.time() * 1000)),
            "engineid": engine_id,
            "requestId": uuid.uuid4().hex,
        }

    def _lasr_request(
        self,
        method: str,
        path: str,
        engine_id: str,
        json_body: Dict[str, Any] | None = None,
        files: Dict[str, Any] | None = None,
        params_extra: Dict[str, str] | None = None,
    ) -> Dict[str, Any]:
        """发送 lasr API 请求，统一处理鉴权和错误。"""
        url = f"{self.LASR_BASE_URL}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }
        params = self._build_lasr_public_params(engine_id)
        if params_extra:
            params.update(params_extra)

        if json_body is not None:
            headers.setdefault("Content-Type", "application/json; charset=utf-8")

        response = _requests.request(
            method=method,
            url=url,
            headers=headers,
            params=params,
            json=json_body,
            files=files,
            timeout=120,
        )

        try:
            data = response.json()
        except Exception:
            raise RuntimeError(
                f"[AIGC] {path} 返回非 JSON 响应: HTTP {response.status_code}, "
                f"body={response.text[:500]}"
            )

        code = data.get("code", -1)
        if code != 0 or response.status_code >= 400:
            desc = data.get("desc", "unknown error")
            raise RuntimeError(
                f"[AIGC] {path} 失败: code={code}, desc={desc}, "
                f"HTTP {response.status_code}, body={json.dumps(data, ensure_ascii=False)[:500]}"
            )

        return data

    # ── Phase 1: 创建音频 ────────────────────────────────────────────

    def _lasr_create(
        self, audio_path: Path, session_id: str, engine_id: str
    ) -> str:
        """Phase 1: POST /lasr/create → audio_id"""
        file_size = audio_path.stat().st_size
        slice_size = self.LASR_SLICE_SIZE

        # 计算 slice_num
        if file_size == 0:
            raise ValueError(f"[AIGC] 音频文件为空: {audio_path}")

        import math
        slice_num = math.ceil(file_size / slice_size)
        if slice_num > 100:
            raise ValueError(
                f"[AIGC] 音频 {file_size / 1024 / 1024:.1f}MB 超过 500MB 限制 "
                f"(slice_num={slice_num} > 100)"
            )

        # 判断 audio_type
        suffix = audio_path.suffix.lower().lstrip(".")
        if suffix == "pcm":
            audio_type = "pcm"
        else:
            audio_type = "auto"

        body = {
            "audio_type": audio_type,
            "x-sessionId": session_id,
            "slice_num": slice_num,
        }

        print(f"[AIGC] /lasr/create: size={file_size / 1024 / 1024:.1f}MB, "
              f"slice_num={slice_num}, audio_type={audio_type}")

        data = self._lasr_request(
            "POST", "/lasr/create", engine_id, json_body=body
        )
        audio_id = data.get("data", {}).get("audio_id", "")
        if not audio_id:
            raise RuntimeError(
                f"[AIGC] /lasr/create 响应中缺少 audio_id: "
                f"{json.dumps(data, ensure_ascii=False)[:300]}"
            )
        return audio_id

    # ── Phase 2: 分片上传 ────────────────────────────────────────────

    def _lasr_upload(
        self, audio_path: Path, audio_id: str, session_id: str, engine_id: str
    ) -> None:
        """Phase 2: POST /lasr/upload（循环每片 5MB）"""
        slice_size = self.LASR_SLICE_SIZE
        file_size = audio_path.stat().st_size
        import math
        slice_num = math.ceil(file_size / slice_size)

        with open(audio_path, "rb") as f:
            for slice_index in range(slice_num):
                offset = slice_index * slice_size
                f.seek(offset)
                chunk_data = f.read(slice_size)

                temp_suffix = audio_path.suffix or ".mp3"
                files = {
                    "file": (
                        f"chunk_{slice_index:03d}{temp_suffix}",
                        chunk_data,
                        "application/octet-stream",
                    )
                }

                params_extra = {
                    "audio_id": audio_id,
                    "x-sessionId": session_id,
                    "slice_index": str(slice_index),
                }

                data = self._lasr_request(
                    "POST",
                    "/lasr/upload",
                    engine_id,
                    files=files,
                    params_extra=params_extra,
                )

                data_inner = data.get("data", {})
                slices = data_inner.get("slices", 0)
                total = data_inner.get("total", 0)

                print(f"[AIGC] /lasr/upload: slice={slice_index + 1}/{slice_num}, "
                      f"server received={slices}/{total}")

        # 验证最后一片
        data_inner = data.get("data", {})
        slices = data_inner.get("slices", 0)
        total = data_inner.get("total", 0)
        if slices != total:
            raise RuntimeError(
                f"[AIGC] /lasr/upload 分片不完整: server slices={slices}, "
                f"client total={total}"
            )

    # ── Phase 3: 创建转写任务 ─────────────────────────────────────────

    def _lasr_run(
        self, audio_id: str, session_id: str, engine_id: str
    ) -> str:
        """Phase 3: POST /lasr/run → task_id"""
        body = {
            "audio_id": audio_id,
            "x-sessionId": session_id,
        }
        data = self._lasr_request("POST", "/lasr/run", engine_id, json_body=body)
        task_id = data.get("data", {}).get("task_id", "")
        if not task_id:
            raise RuntimeError(
                f"[AIGC] /lasr/run 响应中缺少 task_id: "
                f"{json.dumps(data, ensure_ascii=False)[:300]}"
            )
        return task_id

    # ── Phase 4: 轮询进度 ────────────────────────────────────────────

    def _lasr_poll_progress(
        self, task_id: str, session_id: str, engine_id: str
    ) -> None:
        """Phase 4: POST /lasr/progress 轮询至 progress=100"""
        body = {
            "task_id": task_id,
            "x-sessionId": session_id,
        }

        started = time.monotonic()

        for i, interval in enumerate(self.LASR_POLL_INTERVALS):
            elapsed = time.monotonic() - started
            if elapsed > 1.0:  # 首次请求后才 sleep
                time.sleep(interval)

            data = self._lasr_request(
                "POST", "/lasr/progress", engine_id, json_body=body
            )
            progress = data.get("data", {}).get("progress", 0)
            print(f"[AIGC] /lasr/progress: {progress}% (poll #{i + 1})")

            if progress >= 100:
                return

        # 超过内置间隔后，继续用最大间隔轮询
        max_interval = self.LASR_POLL_INTERVALS[-1]
        poll_count = len(self.LASR_POLL_INTERVALS)
        while True:
            elapsed = time.monotonic() - started
            if elapsed > self.LASR_POLL_MAX_SECONDS:
                raise RuntimeError(
                    f"[AIGC] /lasr/progress 轮询超时: "
                    f"{elapsed:.0f}s > {self.LASR_POLL_MAX_SECONDS}s"
                )

            time.sleep(max_interval)
            poll_count += 1
            data = self._lasr_request(
                "POST", "/lasr/progress", engine_id, json_body=body
            )
            progress = data.get("data", {}).get("progress", 0)
            print(f"[AIGC] /lasr/progress: {progress}% (poll #{poll_count})")

            if progress >= 100:
                return

    # ── Phase 5: 获取结果 ─────────────────────────────────────────────

    def _lasr_get_result(
        self, task_id: str, session_id: str, engine_id: str
    ) -> Dict[str, Any]:
        """Phase 5: POST /lasr/result → result data dict"""
        body = {
            "task_id": task_id,
            "x-sessionId": session_id,
        }
        data = self._lasr_request(
            "POST", "/lasr/result", engine_id, json_body=body
        )
        result_inner = data.get("data", {})
        if not isinstance(result_inner, dict):
            # 如果 "data" 不存在，整个响应即为 result
            result_inner = data
        return result_inner
