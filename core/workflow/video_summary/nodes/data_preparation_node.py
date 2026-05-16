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


async def _fetch_image_from_local(path_str: str) -> bytes | None:
    """从本地文件路径读取图片字节（本地开发兼容路径）。"""
    p = Path(path_str)
    if p.exists():
        try:
            return p.read_bytes()
        except Exception as exc:
            logger.debug("Failed to read local keyframe file %s: %s", path_str, exc)
    return None


async def _fetch_keyframe_bytes(oss_key: str) -> bytes | None:
    """
    根据 oss_key 获取关键帧图片字节。
    当前优先尝试本地文件模式；生产环境替换为 OSS 预签名 URL 下载。
    """
    # 尝试本地路径（frames/ 前缀时映射至 TEMP_FRAMES_DIR）
    local_data = await _fetch_image_from_local(oss_key)
    if local_data:
        return local_data

    # 尝试从 TEMP_FRAMES_DIR 中查找仅文件名匹配
    try:
        from config.settings import TEMP_FRAMES_DIR
        fname = Path(oss_key).name
        candidate = TEMP_FRAMES_DIR / fname
        local_data = await _fetch_image_from_local(str(candidate))
        if local_data:
            return local_data
    except Exception:
        pass

    # TODO: step 5.5 后接入 OSS 预签名 URL 下载：
    #   presigned_url = oss_client.generate_presigned_url(oss_key, expires=3600)
    #   async with aiohttp.ClientSession() as session:
    #       async with session.get(presigned_url) as resp:
    #           return await resp.read()

    return None


async def _prepare_keyframes_async(
    keyframes: list[dict[str, Any]],
    max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
    fetch_timeout: float = _DEFAULT_FETCH_TIMEOUT,
) -> list[dict[str, Any]]:
    """
    并发预取关键帧图片，返回每帧增加 image（base64）字段的列表。
    已有 image 字段的帧跳过拉取。
    """
    sem = asyncio.Semaphore(max_concurrency)
    per_frame_timeout = max(1.0, fetch_timeout / max(len(keyframes), 1))

    async def fetch_one(frame: dict[str, Any]) -> dict[str, Any]:
        if frame.get("image"):
            return frame
        source_key = frame.get("oss_key") or frame.get("frame_file")
        if not source_key:
            return frame
        async with sem:
            try:
                raw = await asyncio.wait_for(
                    _fetch_keyframe_bytes(source_key),
                    timeout=per_frame_timeout,
                )
                if raw:
                    return {**frame, "image": base64.b64encode(raw).decode("utf-8")}
            except asyncio.TimeoutError:
                logger.warning(
                    "data_preparation_node: timeout fetching keyframe oss_key=%s", source_key
                )
            except Exception as exc:
                logger.warning(
                    "data_preparation_node: failed to fetch keyframe oss_key=%s: %s",
                    source_key,
                    exc,
                )
        return frame  # 降级：不含 image

    tasks = [fetch_one(f) for f in keyframes]
    return list(await asyncio.gather(*tasks))


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
        return {}

    # 若所有帧已有 image 数据，跳过预取
    missing = [f for f in keyframes if not f.get("image")]
    if not missing:
        return {}

    try:
        enriched = asyncio.run(_prepare_keyframes_async(keyframes))
        fetched_count = sum(1 for f in enriched if f.get("image"))
        logger.info(
            "data_preparation_node: prefetched %d/%d keyframe images",
            fetched_count,
            len(enriched),
        )
        return {"keyframes": enriched}
    except RuntimeError as exc:
        # asyncio.run() 在已有事件循环时会抛出 RuntimeError；降级回退
        logger.warning("data_preparation_node: asyncio.run() conflict, using fallback: %s", exc)
        try:
            loop = asyncio.get_event_loop()
            enriched = loop.run_until_complete(_prepare_keyframes_async(keyframes))
            return {"keyframes": enriched}
        except Exception as inner:
            logger.warning("data_preparation_node: fallback also failed: %s", inner)
            return {}
    except Exception as exc:
        logger.warning("data_preparation_node: degraded fallback due to: %s", exc)
        return {}
