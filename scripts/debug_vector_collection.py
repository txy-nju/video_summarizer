#!/usr/bin/env python
"""向量库调试脚本：查询、验证、诊断 Chroma 向量数据。

用途：
    当 RAG 检索返回空结果（"未找到相关的视频内容"）但向量化任务日志显示
    COMPLETED 时，用此脚本直接查询 Chroma 确认向量是否真正入库，
    并诊断写入端与读取端是否存在路径不一致等问题。

用法：
    # 查看所有 collection 摘要
    python scripts/debug_vector_collection.py --summary

    # 查询 KB 级别 collection
    python scripts/debug_vector_collection.py --collection kb_00897b5cf34f4248985d7a7e915d2369

    # 查询单视频 collection
    python scripts/debug_vector_collection.py --collection video_4e3bfa83-97dc-4d6b-80c9-f376e7bf23ef

    # 按视频 ID 查找（同时检查单视频和 KB collection）
    python scripts/debug_vector_collection.py --video-id 4e3bfa83-97dc-4d6b-80c9-f376e7bf23ef

    # 按知识库 ID 查找
    python scripts/debug_vector_collection.py --kbid 5539b5a3-b9e3-495d-b8bf-dae6b4730828

    # 按 video_id 在特定 KB collection 中查找
    python scripts/debug_vector_collection.py --collection kb_xxx --video-id yyy

    # 显示完整文本内容（默认仅预览 200 字符）
    python scripts/debug_vector_collection.py --collection kb_xxx --full-text

    # 显示所有记录（默认最多 50 条）
    python scripts/debug_vector_collection.py --collection kb_xxx --limit 200

    # 列出所有 metadata 中出现的 collection 值
    python scripts/debug_vector_collection.py --list-collections
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 确保 backend 和 modular_rag 在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _build_chroma_store():
    """按项目配置创建 ChromaStore 实例。"""
    from backend.infrastructure.rag_settings_factory import build_rag_settings
    from modular_rag.libs.vector_store.chroma_store import ChromaStore

    settings = build_rag_settings()
    store = ChromaStore.from_settings(settings.vector_store)
    return store, settings


def _fmt_metadata(meta: dict, max_width: int = 120) -> str:
    """格式化 metadata 为可读字符串，截断过长的值。"""
    parts = []
    for k, v in sorted(meta.items()):
        s = str(v)
        if len(s) > max_width:
            s = s[:max_width] + "..."
        parts.append(f"  {k}: {s}")
    return "\n".join(parts) if parts else "  (empty)"


def cmd_summary():
    """打印 Chroma 整体摘要信息。"""
    store, settings = _build_chroma_store()
    stats = store.get_collection_stats()
    chroma_path = getattr(settings.vector_store, "persist_path", "N/A")

    print("=" * 70)
    print("Chroma 向量库摘要")
    print("=" * 70)
    print(f"  Chroma persist_path : {chroma_path}")
    print(f"  Chroma collection   : {settings.vector_store.collection}")
    print(f"  Total chunk count   : {stats.get('chunk_count', 'N/A')}")
    print()


def cmd_list_collections():
    """列出 Chroma 中所有出现过的 metadata.collection 值及其 chunk 数量。"""
    store, settings = _build_chroma_store()
    stats = store.get_collection_stats()
    total = stats.get("chunk_count", 0)

    if total == 0:
        print("Chroma 中没有任何向量数据。")
        return

    limit = min(total + 100, 50000)
    try:
        payload = store._collection.get(
            limit=limit,
            include=["metadatas"],
        )
    except Exception as exc:
        print(f"获取 Chroma 数据失败: {exc}")
        print(f"Chroma 中共有 {total} 条记录，但无法批量获取 metadata。")
        print("请用 --collection <name> 分别查询。")
        return

    metadatas = payload.get("metadatas", [])
    ids = payload.get("ids", [])

    collection_counter: Counter = Counter()
    sample_ids: dict[str, list[str]] = {}

    for chunk_id, meta in zip(ids, metadatas):
        meta = meta or {}
        coll = meta.get("collection", "(missing)")
        collection_counter[coll] += 1
        if coll not in sample_ids:
            sample_ids[coll] = []
        if len(sample_ids[coll]) < 3:
            sample_ids[coll].append(chunk_id)

    print("=" * 70)
    print("Chroma 中所有 metadata.collection 值分布")
    print("=" * 70)
    print(f"  Chroma persist_path: {getattr(settings.vector_store, 'persist_path', 'N/A')}")
    print(f"  Total chunks scanned: {len(ids)} / {total}")
    print()

    for coll, count in collection_counter.most_common():
        samples = sample_ids.get(coll, [])
        sample_str = ", ".join(samples[:3])
        print(f"  {coll}")
        print(f"    chunks: {count}")
        print(f"    sample_ids: [{sample_str}]")
        print()

    if len(ids) < total:
        print(f"  [WARN] 仅扫描了 {len(ids)}/{total} 条，结果可能不完整。")


def cmd_query_collection(collection: str, video_id: str | None = None,
                         limit: int = 50, full_text: bool = False):
    """查询指定 collection 的向量数据。"""
    store, settings = _build_chroma_store()
    chroma_path = getattr(settings.vector_store, "persist_path", "N/A")
    stats = store.get_collection_stats()

    filters = {"collection": collection}
    if video_id:
        filters["video_id"] = video_id

    filter_desc = " + ".join(f"{k}={v}" for k, v in filters.items())

    print("=" * 70)
    print(f"查询: {filter_desc}")
    print("=" * 70)
    print(f"  Chroma persist_path : {chroma_path}")
    print(f"  Chroma collection   : {settings.vector_store.collection} (physical)")
    print(f"  Total chunks in DB  : {stats.get('chunk_count', 'N/A')}")
    print()

    try:
        probe = store.get_by_metadata(filters, limit=5)
    except Exception as exc:
        print(f"  [FAIL] 查询失败: {exc}")
        return

    if not probe:
        print(f"  [FAIL] 未找到匹配 {filter_desc} 的任何记录！")
        print()
        print("  诊断建议:")
        print(f"    1. 确认 Chroma persist_path: {chroma_path}")
        print(f"    2. 确认 Celery worker 和当前脚本使用同一路径")
        print(f"    3. 尝试 --summary 查看 DB 总体情况")
        print(f"    4. 尝试 --list-collections 查看存在哪些 collection 值")
        print(f"    5. 检查向量化任务日志中 collection 值是否匹配")

        # 展示 DB 中前几条的 collection 值
        try:
            raw = store._collection.get(limit=10, include=["metadatas", "documents"])
            raw_ids = raw.get("ids", [])
            if raw_ids:
                print()
                print(f"  DB 中存在 {len(raw_ids)} 条记录（前10条）:")
                for rid, rmeta in zip(raw_ids, raw.get("metadatas", [])):
                    coll_val = (rmeta or {}).get("collection", "(missing)")
                    vid_val = (rmeta or {}).get("video_id", "(missing)")
                    print(f"    id={rid}")
                    print(f"      collection={coll_val}  video_id={vid_val}")
        except Exception:
            pass
        return

    print(f"  [OK] 探测到数据！前 {min(len(probe), 5)} 条样本:")
    print()

    for i, r in enumerate(probe[:5], 1):
        meta = r.metadata or {}
        text_preview = r.text[:200] if not full_text else r.text
        print(f"  --- Sample #{i} ---")
        print(f"  chunk_id : {r.id}")
        print(f"  score    : {r.score}")
        print(f"  metadata :")
        print(_fmt_metadata(meta))
        print(f"  text     : {text_preview}")
        if not full_text and len(r.text) > 200:
            print(f"            ... (+{len(r.text) - 200} chars, use --full-text for all)")
        print()

    if limit > 5:
        try:
            more = store.get_by_metadata(filters, limit=limit)
        except Exception:
            more = probe

        vid_counter: Counter = Counter()
        for r in more:
            vid = (r.metadata or {}).get("video_id", "(missing)")
            vid_counter[vid] += 1

        print(f"  [STATS] 匹配记录数: {len(more)} (limit={limit})")
        print(f"  [STATS] video_id 分布:")
        for vid, count in vid_counter.most_common():
            print(f"      {vid}: {count} chunks")
        print()


def cmd_find_by_video_id(video_id: str, limit: int = 50, full_text: bool = False):
    """按视频 ID 查找：检查单视频 collection 和所有 KB collection。"""
    store, settings = _build_chroma_store()
    chroma_path = getattr(settings.vector_store, "persist_path", "N/A")

    single_collection = f"video_{video_id}"

    try:
        results = store.get_by_metadata({"video_id": video_id}, limit=limit)
    except Exception as exc:
        print(f"查询失败: {exc}")
        return

    if not results:
        print("=" * 70)
        print(f"按 video_id={video_id} 查找")
        print("=" * 70)
        print(f"  Chroma persist_path: {chroma_path}")
        print(f"  [FAIL] 未找到任何包含 video_id={video_id} 的记录！")
        print()
        print("  该视频可能未被向量化，或向量化到不同的 Chroma 路径。")
        return

    by_collection: dict[str, list] = defaultdict(list)
    for r in results:
        coll = (r.metadata or {}).get("collection", "(missing)")
        by_collection[coll].append(r)

    print("=" * 70)
    print(f"按 video_id={video_id} 查找")
    print("=" * 70)
    print(f"  Chroma persist_path: {chroma_path}")
    print(f"  共找到 {len(results)} 条记录，分布在 {len(by_collection)} 个 collection:")
    print()

    for coll, items in sorted(by_collection.items()):
        print(f"  [collection] {coll}")
        print(f"     chunks: {len(items)}")
        sample = items[0]
        print(f"     sample_chunk_id: {sample.id}")
        print(f"     sample_metadata:")
        print(_fmt_metadata(sample.metadata or {}))
        if full_text:
            print(f"     sample_text: {sample.text[:500]}")
        else:
            print(f"     sample_text: {sample.text[:200]}...")
        print()

    # 验证单视频 collection 是否存在
    if single_collection not in by_collection:
        print(f"  [WARN] 未在单视频 collection ({single_collection}) 中找到记录！")
        print(f"     单视频 QA (stream_video_question) 将无法检索到该视频。")
        print(f"     该视频可能仅在 KB collection 中，或向量化时使用了不同的 collection 名。")


def cmd_find_by_kbid(kbid: str, limit: int = 50, full_text: bool = False):
    """按知识库 ID 查找。"""
    collection_name = f"kb_{kbid}"
    print(f"查询 KB collection: {collection_name}")
    print(f"（如果 KB 使用了自定义 vector_collection_name，请用 --collection 指定）")
    print()
    cmd_query_collection(collection_name, limit=limit, full_text=full_text)


def main():
    parser = argparse.ArgumentParser(
        description="向量库调试脚本 — 查询、验证 Chroma 向量数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--collection", "-c", help="查询指定 metadata.collection 值")
    parser.add_argument("--video-id", "-v", help="按 video_id 查找（搜索所有 collection）")
    parser.add_argument("--kbid", "-k", help="按知识库 ID 查找（等价于 --collection kb_{kbid}）")
    parser.add_argument("--summary", "-s", action="store_true", help="打印 Chroma 整体摘要")
    parser.add_argument("--list-collections", "-l", action="store_true",
                        help="列出所有 metadata.collection 值及其 chunk 数量")
    parser.add_argument("--limit", type=int, default=50, help="最大显示记录数（默认 50）")
    parser.add_argument("--full-text", action="store_true", help="显示完整文本（默认仅预览 200 字符）")

    args = parser.parse_args()

    if not any([args.collection, args.video_id, args.kbid, args.summary, args.list_collections]):
        parser.print_help()
        print()
        print("提示：至少需要指定 --collection, --video-id, --kbid, --summary, 或 --list-collections 中的一个。")
        sys.exit(1)

    if args.summary:
        cmd_summary()

    if args.list_collections:
        cmd_list_collections()

    if args.kbid:
        cmd_find_by_kbid(args.kbid, limit=args.limit, full_text=args.full_text)

    if args.video_id:
        cmd_find_by_video_id(args.video_id, limit=args.limit, full_text=args.full_text)

    if args.collection:
        vid_filter = args.video_id if args.video_id else None
        cmd_query_collection(args.collection, video_id=vid_filter,
                             limit=args.limit, full_text=args.full_text)


if __name__ == "__main__":
    main()
