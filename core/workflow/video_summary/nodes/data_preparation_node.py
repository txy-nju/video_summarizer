"""
多模态数据准备节点（LangGraph）。

在进入视觉/音频分析节点前执行，并发预取关键帧图片至内存（临时 base64），
确保推理节点直接消费已就绪的 image 数据，不在节点内发起阻塞式 OSS 下载。

降级策略：
- 图片拉取失败时保留关键帧元数据（不含 image），不中断工作流。
- 全部预取失败时返回空 dict（不覆盖 state.keyframes），由后续节点按无图片模式处理。

当前实现支持两种图片来源：
1. 本地文件模式：oss_key 或 frame_file 作为本地路径使用（本地开发/测试）
2. OSS 预签名 URL 模式（TODO：step 5.5 后接入 OSS client 生成预签名 URL）
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 关键帧并发拉取上限（避免打爆内存或 OSS 连接池）
_DEFAULT_MAX_CONCURRENCY = 8
# 单次预取超时（秒）
_DEFAULT_FETCH_TIMEOUT = 30.0


def _fetch_image_from_local(path_str: str) -> bytes | None:
    """从本地文件路径读取图片字节（本地开发兼容路径）。"""
    p = Path(path_str)
    if p.exists():
        try:
            return p.read_bytes()
        except Exception as exc:
            logger.debug("Failed to read local keyframe file %s: %s", path_str, exc)
    return None


def _fetch_keyframe_bytes(oss_key: str) -> bytes | None:
    """
    根据 oss_key 获取关键帧图片字节。
    当前优先尝试本地文件模式；生产环境替换为 OSS 预签名 URL 下载。
    """
    # 尝试本地路径（frames/ 前缀时映射至 TEMP_FRAMES_DIR）
    local_data = _fetch_image_from_local(oss_key)
    if local_data:
        return local_data

    # 尝试从 TEMP_FRAMES_DIR 中查找仅文件名匹配
    try:
        from config.settings import TEMP_FRAMES_DIR
        fname = Path(oss_key).name
        candidate = TEMP_FRAMES_DIR / fname
        local_data = _fetch_image_from_local(str(candidate))
        if local_data:
            return local_data
    except Exception:
        pass

    # 尝试从 OSS_LOCAL_ROOT（本地对象存储根目录）解析
    try:
        from config.settings import OSS_LOCAL_ROOT_PATH
        candidate = OSS_LOCAL_ROOT_PATH / oss_key
        local_data = _fetch_image_from_local(str(candidate))
        if local_data:
            return local_data
    except Exception:
        pass

    # TODO: step 5.5 后接入 OSS 预签名 URL 下载：
    #   presigned_url = oss_client.generate_presigned_url(oss_key, expires=3600)
    #   import requests
    #   resp = requests.get(presigned_url, timeout=5)
    #   if resp.status_code == 200:
    #       return resp.content

    return None


def _prepare_keyframes_sync(
    keyframes: list[dict[str, Any]],
    max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
    fetch_timeout: float = _DEFAULT_FETCH_TIMEOUT,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """
    并发预取关键帧图片，返回每帧增加 image（base64）字段的列表。
    已有 image 字段的帧跳过拉取。
    """
    import concurrent.futures

    per_frame_timeout = max(1.0, fetch_timeout / max(len(keyframes), 1))

    def fetch_one(frame: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if frame.get("image"):
            return frame, "already"
        source_key = frame.get("oss_key") or frame.get("frame_file")
        if not source_key:
            return frame, "missing_source"
        try:
            # 执行同步的文件读取/下载
            raw = _fetch_keyframe_bytes(source_key)
            if raw:
                return {**frame, "image": base64.b64encode(raw).decode("utf-8")}, "fetched"
        except Exception as exc:
            logger.warning(
                "data_preparation_node: failed to fetch keyframe oss_key=%s: %s",
                source_key,
                exc,
            )
            return frame, "error"
        return frame, "not_found"

    stats = {
        "fetched": 0,
        "already": 0,
        "timeout": 0,
        "error": 0,
        "missing_source": 0,
        "not_found": 0,
    }
    enriched = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures = {executor.submit(fetch_one, f): f for f in keyframes}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=fetch_timeout):
                try:
                    item, status = future.result()
                    enriched.append(item)
                    stats[status] += 1
                except Exception as exc:
                    frame = futures[future]
                    enriched.append(frame)
                    stats["error"] += 1
                    logger.warning("data_preparation_node: task failed: %s", exc)
        except concurrent.futures.TimeoutError:
            logger.warning("data_preparation_node: prefetch execution timed out globally")
            # 填充剩余未完成的任务
            for future, frame in futures.items():
                if not future.done():
                    future.cancel()
                    enriched.append(frame)
                    stats["timeout"] += 1

    # 按照输入的 keyframes 顺序排列结果
    enriched_by_key = {}
    for f in enriched:
        key = f.get("oss_key") or f.get("frame_file") or ""
        if key:
            enriched_by_key[key] = f

    ordered_enriched = []
    for f in keyframes:
        key = f.get("oss_key") or f.get("frame_file") or ""
        ordered_enriched.append(enriched_by_key.get(key, f))

    return ordered_enriched, stats


def _record_observable_event(event: dict[str, Any]) -> None:
    try:
        from backend.services.task_status_service import TaskStatusService

        TaskStatusService.record_observable_event(event)
    except Exception as exc:
        logger.debug("data_preparation_node: observable event sink unavailable: %s", exc)


def _build_recoverable_error(
    *,
    message: str,
    details: dict[str, Any],
    retry_after: int = 5,
) -> dict[str, Any]:
    return {
        "code": "DATA_PREPARATION_DEGRADED",
        "message": message,
        "details": details,
        "is_retryable": True,
        "retry_after": retry_after,
    }


def data_preparation_node(state: dict[str, Any]) -> dict[str, Any]:
    """
    LangGraph 节点：在多模态推理节点前并发预取关键帧图片。

    输入消费：state["keyframes"]（含 oss_key 或 frame_file）
    输出写入：state["keyframes"]（每帧增加 image 字段，降级时保持原样）

    约束：
    - 推理节点不得在节点内发起额外 OSS 下载。
    - 此节点的数据准备失败采用降级策略，不中断工作流。
    """
    keyframes: list[dict[str, Any]] = state.get("keyframes") or []
    if not keyframes:
        return {
            "data_preparation_status": {
                "status": "skipped",
                "fetched": 0,
                "total": 0,
                "error": None,
            }
        }

    # 若所有帧已有 image 数据，跳过预取
    missing = [f for f in keyframes if not f.get("image")]
    if not missing:
        return {
            "data_preparation_status": {
                "status": "completed",
                "fetched": len(keyframes),
                "total": len(keyframes),
                "error": None,
            }
        }

    try:
        enriched, stats = _prepare_keyframes_sync(keyframes)
        fetched_count = stats.get("fetched", 0)
        missing_count = len(missing)
        failed_count = max(0, missing_count - fetched_count)
        logger.info(
            "data_preparation_node: prefetched %d/%d keyframe images",
            fetched_count,
            len(enriched),
        )

        if failed_count > 0:
            error_payload = _build_recoverable_error(
                message="Keyframe prefetch partially failed; continue with degraded context.",
                details={
                    "total_keyframes": len(keyframes),
                    "missing_images": missing_count,
                    "fetched_images": fetched_count,
                    "timeout_count": stats.get("timeout", 0),
                    "error_count": stats.get("error", 0),
                    "not_found_count": stats.get("not_found", 0),
                },
                retry_after=5,
            )
            event = {
                "event_type": "status_update",
                "scope": "video_summary_task",
                "scope_id": str(state.get("thread_id", "")),
                "node": "data_preparation_node",
                "status": "DEGRADED",
                "progress": 100,
                "payload": error_payload,
            }
            _record_observable_event(event)
            return {
                "keyframes": enriched,
                "data_preparation_status": {
                    "status": "degraded",
                    "fetched": fetched_count,
                    "total": len(keyframes),
                    "error": error_payload,
                },
                "data_preparation_events": [event],
            }

        return {
            "keyframes": enriched,
            "data_preparation_status": {
                "status": "completed",
                "fetched": len(keyframes),
                "total": len(keyframes),
                "error": None,
            },
        }
    except Exception as exc:
        logger.warning("data_preparation_node: degraded fallback due to: %s", exc)
        error_payload = _build_recoverable_error(
            message="data_preparation degraded due to runtime error",
            details={"exception": str(exc)},
            retry_after=5,
        )
        event = {
            "event_type": "status_update",
            "scope": "video_summary_task",
            "scope_id": str(state.get("thread_id", "")),
            "node": "data_preparation_node",
            "status": "DEGRADED",
            "progress": 100,
            "payload": error_payload,
        }
        _record_observable_event(event)
        return {
            "keyframes": keyframes,  # 确保返回原始 keyframes 以免数据丢失
            "data_preparation_status": {
                "status": "degraded",
                "fetched": 0,
                "total": len(keyframes),
                "error": error_payload,
            },
            "data_preparation_events": [event],
        }
