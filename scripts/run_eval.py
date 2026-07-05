import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.llm_as_a_judge import score_claim_based_hallucination, score_task_alignment
from evaluation.qa_judge import evaluate_qa_answer
from services.workflow_service import VideoSummaryService


@dataclass
class QAPair:
    question: str
    timestamp: str
    reference_answer: str
    key_points: List[str]


@dataclass
class EvalSample:
    sample_id: str
    enabled: bool
    source_type: str
    video_source: str
    user_prompt: str
    reference_summary: str
    key_points: List[str]
    tags: List[str]
    human_guidance: str
    edited_aggregated_chunk_insights: str
    qa_pairs: List[QAPair]


@dataclass
class JudgeResult:
    label: str
    score: Optional[float]
    details: str
    metrics: Dict[str, Any]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(content)


def _to_list_of_str(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return []


def _normalize_sample(raw: Dict[str, Any]) -> EvalSample:
    sample_id = str(raw.get("sample_id", "")).strip()
    source_type = str(raw.get("source_type", "")).strip().lower()
    if not sample_id:
        raise ValueError("sample_id is required")
    if source_type not in {"url", "local"}:
        raise ValueError(f"sample {sample_id}: source_type must be 'url' or 'local'")

    video_source = str(raw.get("video_source", "")).strip()
    if not video_source:
        raise ValueError(f"sample {sample_id}: video_source is required")

    return EvalSample(
        sample_id=sample_id,
        enabled=bool(raw.get("enabled", True)),
        source_type=source_type,
        video_source=video_source,
        user_prompt=str(raw.get("user_prompt", "")).strip(),
        reference_summary=str(raw.get("reference_summary", "")).strip(),
        key_points=_to_list_of_str(raw.get("key_points", [])),
        tags=_to_list_of_str(raw.get("tags", [])),
        human_guidance=str(raw.get("human_guidance", "")).strip(),
        edited_aggregated_chunk_insights=str(raw.get("edited_aggregated_chunk_insights", "")).strip(),
        qa_pairs=_normalize_qa_pairs(raw.get("qa_pairs", [])),
    )


def _normalize_qa_pairs(raw_qa_pairs: Any) -> List[QAPair]:
    """从数据集 JSON 中解析 qa_pairs 数组。"""
    if not isinstance(raw_qa_pairs, list):
        return []
    pairs: List[QAPair] = []
    for item in raw_qa_pairs:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        timestamp = str(item.get("timestamp", "")).strip()
        if not question or not timestamp:
            continue
        pairs.append(
            QAPair(
                question=question,
                timestamp=timestamp,
                reference_answer=str(item.get("reference_answer", "")).strip(),
                key_points=_to_list_of_str(item.get("key_points", [])),
            )
        )
    return pairs


def _load_samples(dataset_path: Path) -> List[EvalSample]:
    data = _load_json(dataset_path)
    if not isinstance(data, list):
        raise ValueError("dataset must be a JSON array")
    return [_normalize_sample(item) for item in data if isinstance(item, dict)]


def _build_reference_evidence(sample: EvalSample) -> str:
    parts: List[str] = []
    if sample.reference_summary:
        parts.append("[reference_summary]\n" + sample.reference_summary)
    if sample.key_points:
        bullets = "\n".join(f"- {point}" for point in sample.key_points)
        parts.append("[key_points]\n" + bullets)
    return "\n\n".join(parts).strip()


def _run_fact_judge(
    generated_summary: str,
    reference_evidence: str,
    api_key: str,
    base_url: Optional[str],
    sample_id: str,
    judge_model: Optional[str] = None,
) -> JudgeResult:
    print(f"  [Judge:Fact] sample={sample_id} 开始声明级幻觉评分（Claim Extraction -> Claim Verification）")
    if not reference_evidence:
        print(f"  [Judge:Fact] sample={sample_id} 跳过：reference_evidence 为空")
        return JudgeResult(label="na", score=None, details="reference evidence missing", metrics={})

    try:
        result = score_claim_based_hallucination(
            generated_summary=generated_summary,
            reference_evidence=reference_evidence,
            api_key=api_key,
            base_url=base_url,
            model_name=judge_model,
        )
        print(
            "  [Judge:Fact] sample={} 完成：label={} score={} claims={} support_ratio={} hallucination_density={}".format(
                sample_id,
                result.get("label", "na"),
                result.get("score"),
                result.get("claim_count", 0),
                result.get("support_ratio"),
                result.get("hallucination_density"),
            )
        )
        return JudgeResult(
            label=str(result.get("label", "na")),
            score=result.get("score"),
            details=str(result.get("details", "")),
            metrics=result,
        )
    except Exception as exc:
        print(f"  [Judge:Fact] sample={sample_id} 异常：{exc}")
        return JudgeResult(label="na", score=None, details=str(exc), metrics={})


def _run_task_judge(
    generated_summary: str,
    user_prompt: str,
    human_guidance: str,
    api_key: str,
    base_url: Optional[str],
    sample_id: str,
    judge_model: Optional[str] = None,
) -> JudgeResult:
    print(f"  [Judge:Task] sample={sample_id} 开始任务对齐评分（Requirement Extraction -> Requirement Verification）")
    if not user_prompt and not human_guidance:
        print(f"  [Judge:Task] sample={sample_id} 跳过：user_prompt 与 human_guidance 均为空")
        return JudgeResult(
            label="na",
            score=None,
            details="user_prompt and human_guidance missing",
            metrics={},
        )

    try:
        result = score_task_alignment(
            generated_summary=generated_summary,
            user_prompt=user_prompt,
            human_guidance=human_guidance,
            api_key=api_key,
            base_url=base_url,
            model_name=judge_model,
        )
        print(
            "  [Judge:Task] sample={} 完成：label={} score={} requirements={} coverage_ratio={}".format(
                sample_id,
                result.get("label", "na"),
                result.get("score"),
                result.get("requirement_count", 0),
                result.get("coverage_ratio"),
            )
        )
        return JudgeResult(
            label=str(result.get("label", "na")),
            score=result.get("score"),
            details=str(result.get("details", "")),
            metrics=result,
        )
    except Exception as exc:
        print(f"  [Judge:Task] sample={sample_id} 异常：{exc}")
        return JudgeResult(label="na", score=None, details=str(exc), metrics={})


def _safe_mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _iso_now() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 4)


def _metric_values(results: List[Dict[str, Any]], field: str) -> List[float]:
    values: List[float] = []
    for item in results:
        value = item.get(field)
        if value is None:
            continue
        values.append(float(value))
    return values


def _metric_int_sum(results: List[Dict[str, Any]], field: str) -> int:
    total = 0
    for item in results:
        value = item.get(field)
        if value is None:
            continue
        total += int(value)
    return total


def _check_duplicate_sample_ids(samples: List[EvalSample]) -> None:
    """Check for duplicate enabled sample IDs and raise error if found."""
    seen_ids: Dict[str, int] = {}
    for idx, sample in enumerate(samples):
        if sample.sample_id in seen_ids:
            first_idx = seen_ids[sample.sample_id]
            raise ValueError(
                f"Duplicate enabled sample_id detected: '{sample.sample_id}' "
                f"at positions {first_idx} and {idx}. "
                f"Each enabled sample must have a unique sample_id within a run."
            )
        seen_ids[sample.sample_id] = idx


def _render_markdown(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    summary = report["summary"]

    lines.append("# Offline Evaluation Report")
    lines.append("")
    lines.append(f"- run_id: {report['run_id']}")
    lines.append(f"- generated_at: {report['generated_at']}")
    lines.append(f"- dataset_path: {report['dataset_path']}")
    lines.append("")

    lines.append("## Overview")
    lines.append("")
    lines.append(f"- total_samples: {summary['total_samples']}")
    lines.append(f"- enabled_samples: {summary['enabled_samples']}")
    lines.append(f"- success_samples: {summary['success_samples']}")
    lines.append(f"- failed_samples: {summary['failed_samples']}")
    lines.append(f"- fact_avg_score: {summary['fact_avg_score']}")
    lines.append(f"- task_avg_score: {summary['task_avg_score']}")
    lines.append(f"- fact_judge_confidence_avg: {summary['fact_judge_confidence_avg']}")
    lines.append(f"- task_judge_confidence_avg: {summary['task_judge_confidence_avg']}")
    lines.append(f"- support_ratio_avg: {summary['support_ratio_avg']}")
    lines.append(f"- task_coverage_ratio_avg: {summary['task_coverage_ratio_avg']}")
    lines.append(f"- hallucination_density_avg: {summary['hallucination_density_avg']}")
    lines.append(f"- weighted_hallucination_density_avg: {summary['weighted_hallucination_density_avg']}")
    lines.append(f"- total_claim_importance: {summary['total_claim_importance']}")
    lines.append(f"- total_requirement_importance: {summary['total_requirement_importance']}")
    lines.append(f"- total_entity_hallucinations: {summary['total_entity_hallucinations']}")
    lines.append(f"- total_relation_action_hallucinations: {summary['total_relation_action_hallucinations']}")
    lines.append(f"- total_fabrications: {summary['total_fabrications']}")
    lines.append(f"- qa_total_pairs: {summary.get('qa_total_pairs', 0)}")
    lines.append(f"- qa_passed_pairs: {summary.get('qa_passed_pairs', 0)}")
    lines.append(f"- qa_failed_pairs: {summary.get('qa_failed_pairs', 0)}")
    lines.append(f"- qa_avg_score: {summary.get('qa_avg_score')}")
    lines.append(f"- qa_avg_confidence: {summary.get('qa_avg_confidence')}")
    lines.append("")

    lines.append("## Low Score Samples")
    lines.append("")
    if report["low_score_samples"]:
        for item in report["low_score_samples"]:
            lines.append(
                f"- {item['sample_id']}: fact={item['fact_label']}({item['fact_score']}) task={item['task_label']}({item['task_score']}) fact_confidence={item['fact_judge_confidence']} hallucination_density={item['hallucination_density']} output={item['final_summary_path']}"
            )
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Per Sample Outputs")
    lines.append("")
    for item in report["results"]:
        lines.append(
            f"- {item['sample_id']}: status={item['status']} fact={item['fact_label']}({item['fact_score']}) task={item['task_label']}({item['task_score']}) fact_confidence={item['fact_judge_confidence']} task_confidence={item['task_judge_confidence']} hallucination_density={item['hallucination_density']} output={item['final_summary_path']}"
        )

    return "\n".join(lines) + "\n"


def _compare_with_baseline(current_report: Dict[str, Any], baseline_report: Dict[str, Any]) -> Dict[str, Any]:
    cur = current_report["summary"]
    base = baseline_report.get("summary", {})

    current_fact = cur.get("fact_avg_score")
    baseline_fact = base.get("fact_avg_score")
    current_task = cur.get("task_avg_score")
    baseline_task = base.get("task_avg_score")
    current_fact_confidence = cur.get("fact_judge_confidence_avg")
    baseline_fact_confidence = base.get("fact_judge_confidence_avg")
    current_task_confidence = cur.get("task_judge_confidence_avg")
    baseline_task_confidence = base.get("task_judge_confidence_avg")
    current_hall_density = cur.get("hallucination_density_avg")
    baseline_hall_density = base.get("hallucination_density_avg")
    current_weighted_hall_density = cur.get("weighted_hallucination_density_avg")
    baseline_weighted_hall_density = base.get("weighted_hallucination_density_avg")

    return {
        "baseline_run_id": baseline_report.get("run_id", "unknown"),
        "current_run_id": current_report.get("run_id", "unknown"),
        "fact_delta": _delta(current_fact, baseline_fact),
        "task_delta": _delta(current_task, baseline_task),
        "fact_confidence_delta": _delta(current_fact_confidence, baseline_fact_confidence),
        "task_confidence_delta": _delta(current_task_confidence, baseline_task_confidence),
        "hallucination_density_delta": _delta(current_hall_density, baseline_hall_density),
        "weighted_hallucination_density_delta": _delta(
            current_weighted_hall_density, baseline_weighted_hall_density
        ),
        "current_fact_avg": current_fact,
        "baseline_fact_avg": baseline_fact,
        "current_task_avg": current_task,
        "baseline_task_avg": baseline_task,
        "current_fact_confidence_avg": current_fact_confidence,
        "baseline_fact_confidence_avg": baseline_fact_confidence,
        "current_task_confidence_avg": current_task_confidence,
        "baseline_task_confidence_avg": baseline_task_confidence,
        "current_hallucination_density_avg": current_hall_density,
        "baseline_hallucination_density_avg": baseline_hall_density,
        "current_weighted_hallucination_density_avg": current_weighted_hall_density,
        "baseline_weighted_hallucination_density_avg": baseline_weighted_hall_density,
    }


def run_eval(
    dataset_path: Path,
    output_dir: Path,
    run_id: str,
    max_samples: Optional[int],
    sample_ids: Optional[List[str]],
    api_key: str,
    base_url: Optional[str],
    baseline_report_path: Optional[Path],
    judge_model: Optional[str] = None,
) -> Dict[str, Any]:
    samples = _load_samples(dataset_path)
    enabled_samples = [s for s in samples if s.enabled]

    if sample_ids:
        chosen = set(sample_ids)
        enabled_samples = [s for s in enabled_samples if s.sample_id in chosen]

    # Check for duplicate sample IDs
    _check_duplicate_sample_ids(enabled_samples)

    if max_samples is not None and max_samples >= 0:
        enabled_samples = enabled_samples[:max_samples]

    service = VideoSummaryService(api_key=api_key, base_url=base_url)

    run_root = output_dir / run_id
    samples_root = run_root / "samples"
    samples_root.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []

    for idx, sample in enumerate(enabled_samples, start=1):
        sample_started_at = time.perf_counter()
        sample_dir = samples_root / sample.sample_id
        sample_dir.mkdir(parents=True, exist_ok=True)
        print(f"[{idx}/{len(enabled_samples)}] running sample={sample.sample_id}")

        try:
            thread_id = f"eval-{run_id}-{sample.sample_id}"

            analyze_started = time.perf_counter()
            if sample.source_type == "url":
                review_package = service.analyze_url_video(
                    url=sample.video_source,
                    user_prompt=sample.user_prompt,
                    thread_id=thread_id,
                )
            else:
                local_path = (PROJECT_ROOT / sample.video_source).resolve()
                if not local_path.exists():
                    raise FileNotFoundError(f"local file not found: {local_path}")

                with local_path.open("rb") as f:
                    review_package = service.analyze_uploaded_video(
                        uploaded_file=f,
                        original_filename=local_path.name,
                        user_prompt=sample.user_prompt,
                        thread_id=thread_id,
                    )
            analyze_ms = int((time.perf_counter() - analyze_started) * 1000)

            edited_aggregated_chunk_insights = sample.edited_aggregated_chunk_insights
            if not edited_aggregated_chunk_insights:
                edited_aggregated_chunk_insights = str(
                    review_package.get("editable_aggregated_chunk_insights", "")
                )

            finalize_started = time.perf_counter()
            final_summary = service.finalize_summary(
                thread_id=str(review_package.get("thread_id", thread_id)),
                edited_aggregated_chunk_insights=edited_aggregated_chunk_insights,
                human_guidance=sample.human_guidance,
            )
            finalize_ms = int((time.perf_counter() - finalize_started) * 1000)

            reference_evidence = _build_reference_evidence(sample)
            print(f"  [Judge] sample={sample.sample_id} 并发启动 Fact/Task 评分")
            with ThreadPoolExecutor(max_workers=2) as executor:
                fact_future = executor.submit(
                    _run_fact_judge,
                    final_summary,
                    reference_evidence,
                    api_key,
                    base_url,
                    sample.sample_id,
                    judge_model,
                )
                task_future = executor.submit(
                    _run_task_judge,
                    final_summary,
                    sample.user_prompt,
                    sample.human_guidance,
                    api_key,
                    base_url,
                    sample.sample_id,
                    judge_model,
                )
                fact_judge = fact_future.result()
                task_judge = task_future.result()
            print(f"  [Judge] sample={sample.sample_id} Fact/Task 评分已汇总")

            # ── 时间旅行追问评估 ──────────────────────────
            qa_results: List[Dict[str, Any]] = []
            if sample.qa_pairs:
                print(f"  [Judge:QA] sample={sample.sample_id} 开始时间旅行追问评估（共 {len(sample.qa_pairs)} 个问题）")
                thread_id_for_qa = str(review_package.get("thread_id", thread_id))
                for qa_idx, qa_pair in enumerate(sample.qa_pairs, start=1):
                    qa_label = f"{sample.sample_id}/qa-{qa_idx}"
                    try:
                        qa_started = time.perf_counter()
                        qa_answer = service.ask_at_timestamp(
                            timestamp=qa_pair.timestamp,
                            question=qa_pair.question,
                            thread_id=thread_id_for_qa,
                        )
                        qa_ms = int((time.perf_counter() - qa_started) * 1000)

                        qa_judge = evaluate_qa_answer(
                            generated_answer=qa_answer,
                            question=qa_pair.question,
                            timestamp=qa_pair.timestamp,
                            reference_answer=qa_pair.reference_answer,
                            reference_key_points=qa_pair.key_points,
                            api_key=api_key,
                            base_url=base_url,
                            model_name=judge_model,
                        )
                        print(
                            f"    [Judge:QA] {qa_label}: label={qa_judge.get('label')} "
                            f"score={qa_judge.get('score')} confidence={qa_judge.get('judge_confidence')}"
                        )
                        qa_results.append(
                            {
                                "qa_index": qa_idx,
                                "question": qa_pair.question,
                                "timestamp": qa_pair.timestamp,
                                "generated_answer": qa_answer,
                                "reference_answer": qa_pair.reference_answer,
                                "reference_key_points": qa_pair.key_points,
                                "judge": qa_judge,
                                "timing_ms": qa_ms,
                            }
                        )
                    except Exception as exc:
                        print(f"    [Judge:QA] {qa_label} 异常：{exc}")
                        qa_results.append(
                            {
                                "qa_index": qa_idx,
                                "question": qa_pair.question,
                                "timestamp": qa_pair.timestamp,
                                "generated_answer": "",
                                "reference_answer": qa_pair.reference_answer,
                                "reference_key_points": qa_pair.key_points,
                                "judge": {
                                    "score": None,
                                    "label": "na",
                                    "details": str(exc),
                                    "dimensions": {},
                                    "judge_confidence": 0,
                                    "judge_reasoning": "",
                                },
                                "timing_ms": 0,
                                "error": str(exc),
                            }
                        )

            final_summary_path = sample_dir / "final_summary.md"
            sample_result_path = sample_dir / "result.json"
            _write_text(final_summary_path, final_summary)
            _write_json(
                sample_result_path,
                {
                    "sample_id": sample.sample_id,
                    "source_type": sample.source_type,
                    "video_source": sample.video_source,
                    "review_package": review_package,
                    "judge": {
                        "fact": {
                            "label": fact_judge.label,
                            "score": fact_judge.score,
                            "details": fact_judge.details,
                            "metrics": fact_judge.metrics,
                        },
                        "task": {
                            "label": task_judge.label,
                            "score": task_judge.score,
                            "details": task_judge.details,
                            "metrics": task_judge.metrics,
                        },
                        "qa": qa_results,
                    },
                    "timing": {
                        "analyze_ms": analyze_ms,
                        "finalize_ms": finalize_ms,
                        "total_ms": int((time.perf_counter() - sample_started_at) * 1000),
                    },
                },
            )

            # ── QA 聚合指标（跨该样本的所有 qa_pairs） ──
            qa_scores = [
                qr["judge"]["score"]
                for qr in qa_results
                if qr["judge"].get("score") is not None
            ]
            qa_confidence_values = [
                qr["judge"].get("judge_confidence", 0)
                for qr in qa_results
                if qr["judge"].get("score") is not None
            ]
            qa_total = len(qa_results)
            qa_passed = sum(1 for qr in qa_results if qr["judge"].get("label") == "qa-pass")
            qa_warned = sum(1 for qr in qa_results if qr["judge"].get("label") == "qa-warn")
            qa_failed = sum(1 for qr in qa_results if qr["judge"].get("label") == "qa-fail")
            qa_na = sum(1 for qr in qa_results if qr["judge"].get("label") == "na")

            results.append(
                {
                    "sample_id": sample.sample_id,
                    "status": "success",
                    "source_type": sample.source_type,
                    "video_source": sample.video_source,
                    "thread_id": str(review_package.get("thread_id", thread_id)),
                    "fact_label": fact_judge.label,
                    "fact_score": fact_judge.score,
                    "fact_details": fact_judge.details,
                    "fact_judge_confidence": fact_judge.metrics.get("judge_confidence"),
                    "claim_count": fact_judge.metrics.get("claim_count", 0),
                    "total_claim_importance": fact_judge.metrics.get("total_claim_importance", 0),
                    "supported_claim_count": fact_judge.metrics.get("supported_claim_count", 0),
                    "contradicted_claim_count": fact_judge.metrics.get("contradicted_claim_count", 0),
                    "not_mentioned_claim_count": fact_judge.metrics.get("not_mentioned_claim_count", 0),
                    "supported_claim_importance": fact_judge.metrics.get("supported_claim_importance", 0),
                    "contradicted_claim_importance": fact_judge.metrics.get("contradicted_claim_importance", 0),
                    "not_mentioned_claim_importance": fact_judge.metrics.get("not_mentioned_claim_importance", 0),
                    "support_ratio": fact_judge.metrics.get("support_ratio"),
                    "hallucination_density": fact_judge.metrics.get("hallucination_density"),
                    "weighted_hallucination_density": fact_judge.metrics.get("weighted_hallucination_density"),
                    "weighted_penalty": fact_judge.metrics.get("weighted_penalty"),
                    "hallucination_breakdown": fact_judge.metrics.get("hallucination_breakdown", {}),
                    "weighted_hallucination_penalty_breakdown": fact_judge.metrics.get(
                        "weighted_hallucination_penalty_breakdown", {}
                    ),
                    "task_label": task_judge.label,
                    "task_score": task_judge.score,
                    "task_details": task_judge.details,
                    "task_judge_confidence": task_judge.metrics.get("judge_confidence"),
                    "requirement_count": task_judge.metrics.get("requirement_count", 0),
                    "total_requirement_importance": task_judge.metrics.get("total_requirement_importance", 0),
                    "satisfied_requirement_count": task_judge.metrics.get("satisfied_requirement_count", 0),
                    "partial_requirement_count": task_judge.metrics.get("partial_requirement_count", 0),
                    "unsatisfied_requirement_count": task_judge.metrics.get("unsatisfied_requirement_count", 0),
                    "satisfied_requirement_importance": task_judge.metrics.get("satisfied_requirement_importance", 0),
                    "partial_requirement_importance": task_judge.metrics.get("partial_requirement_importance", 0),
                    "unsatisfied_requirement_importance": task_judge.metrics.get("unsatisfied_requirement_importance", 0),
                    "task_coverage_ratio": task_judge.metrics.get("coverage_ratio"),
                    # QA 摘要
                    "qa_total": qa_total,
                    "qa_passed": qa_passed,
                    "qa_warned": qa_warned,
                    "qa_failed": qa_failed,
                    "qa_na": qa_na,
                    "qa_avg_score": _safe_mean(qa_scores),
                    "qa_avg_confidence": _safe_mean(qa_confidence_values),
                    "qa_results": qa_results,
                    # 耗时
                    "analyze_ms": analyze_ms,
                    "finalize_ms": finalize_ms,
                    "total_ms": int((time.perf_counter() - sample_started_at) * 1000),
                    "final_summary_path": str(final_summary_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                    "sample_result_path": str(sample_result_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                }
            )
        except Exception as exc:
            error_path = sample_dir / "error.txt"
            _write_text(error_path, str(exc) + "\n")
            results.append(
                {
                    "sample_id": sample.sample_id,
                    "status": "failed",
                    "source_type": sample.source_type,
                    "video_source": sample.video_source,
                    "thread_id": "",
                    "fact_label": "na",
                    "fact_score": None,
                    "fact_details": "",
                    "fact_judge_confidence": None,
                    "claim_count": 0,
                    "total_claim_importance": 0,
                    "supported_claim_count": 0,
                    "contradicted_claim_count": 0,
                    "not_mentioned_claim_count": 0,
                    "supported_claim_importance": 0,
                    "contradicted_claim_importance": 0,
                    "not_mentioned_claim_importance": 0,
                    "support_ratio": None,
                    "hallucination_density": None,
                    "weighted_hallucination_density": None,
                    "weighted_penalty": None,
                    "hallucination_breakdown": {},
                    "weighted_hallucination_penalty_breakdown": {},
                    "task_label": "na",
                    "task_score": None,
                    "task_details": "",
                    "task_judge_confidence": None,
                    "requirement_count": 0,
                    "total_requirement_importance": 0,
                    "satisfied_requirement_count": 0,
                    "partial_requirement_count": 0,
                    "unsatisfied_requirement_count": 0,
                    "satisfied_requirement_importance": 0,
                    "partial_requirement_importance": 0,
                    "unsatisfied_requirement_importance": 0,
                    "task_coverage_ratio": None,
                    "qa_total": 0,
                    "qa_passed": 0,
                    "qa_warned": 0,
                    "qa_failed": 0,
                    "qa_na": 0,
                    "qa_avg_score": None,
                    "qa_avg_confidence": None,
                    "qa_results": [],
                    "analyze_ms": 0,
                    "finalize_ms": 0,
                    "total_ms": int((time.perf_counter() - sample_started_at) * 1000),
                    "error": str(exc),
                    "final_summary_path": "",
                    "sample_result_path": str((sample_dir / "error.txt").relative_to(PROJECT_ROOT)).replace("\\", "/"),
                }
            )
            print(f"  sample failed: {sample.sample_id}, error={exc}")

    fact_values = _metric_values(results, "fact_score")
    task_values = _metric_values(results, "task_score")
    fact_confidence_values = _metric_values(results, "fact_judge_confidence")
    task_confidence_values = _metric_values(results, "task_judge_confidence")
    support_ratio_values = _metric_values(results, "support_ratio")
    task_coverage_values = _metric_values(results, "task_coverage_ratio")
    hallucination_density_values = _metric_values(results, "hallucination_density")
    weighted_hallucination_density_values = _metric_values(results, "weighted_hallucination_density")

    total_entity_hallucinations = sum(
        int(item.get("hallucination_breakdown", {}).get("entity", 0) or 0) for item in results
    )
    total_relation_action_hallucinations = sum(
        int(item.get("hallucination_breakdown", {}).get("relation_action", 0) or 0) for item in results
    )
    total_fabrications = sum(
        int(item.get("hallucination_breakdown", {}).get("fabrication", 0) or 0) for item in results
    )
    total_claim_importance = _metric_int_sum(results, "total_claim_importance")
    total_requirement_importance = _metric_int_sum(results, "total_requirement_importance")

    # QA 汇聚
    qa_all_scores: List[float] = []
    qa_all_confidence: List[float] = []
    qa_total_pairs = 0
    qa_total_passed = 0
    qa_total_failed = 0
    for item in results:
        if item["status"] != "success":
            continue
        qa_total_pairs += int(item.get("qa_total", 0))
        qa_total_passed += int(item.get("qa_passed", 0))
        qa_total_failed += int(item.get("qa_failed", 0))
        qa_score = item.get("qa_avg_score")
        qa_conf = item.get("qa_avg_confidence")
        if qa_score is not None:
            qa_all_scores.append(float(qa_score))
        if qa_conf is not None:
            qa_all_confidence.append(float(qa_conf))

    low_score_samples = [
        item
        for item in results
        if item["status"] == "success"
        and (item.get("fact_label") == "fail" or item.get("task_label") == "fail")
    ]

    report: Dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(),
        "dataset_path": str(dataset_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "summary": {
            "total_samples": len(samples),
            "enabled_samples": len(enabled_samples),
            "success_samples": len([x for x in results if x["status"] == "success"]),
            "failed_samples": len([x for x in results if x["status"] == "failed"]),
            "fact_avg_score": _safe_mean(fact_values),
            "task_avg_score": _safe_mean(task_values),
            "fact_judge_confidence_avg": _safe_mean(fact_confidence_values),
            "task_judge_confidence_avg": _safe_mean(task_confidence_values),
            "support_ratio_avg": _safe_mean(support_ratio_values),
            "task_coverage_ratio_avg": _safe_mean(task_coverage_values),
            "hallucination_density_avg": _safe_mean(hallucination_density_values),
            "weighted_hallucination_density_avg": _safe_mean(weighted_hallucination_density_values),
            "total_claim_importance": total_claim_importance,
            "total_requirement_importance": total_requirement_importance,
            "total_entity_hallucinations": total_entity_hallucinations,
            "total_relation_action_hallucinations": total_relation_action_hallucinations,
            "total_fabrications": total_fabrications,
            "qa_total_pairs": qa_total_pairs,
            "qa_passed_pairs": qa_total_passed,
            "qa_failed_pairs": qa_total_failed,
            "qa_avg_score": _safe_mean(qa_all_scores),
            "qa_avg_confidence": _safe_mean(qa_all_confidence),
            "qa_samples_with_pairs": len(qa_all_scores),
        },
        "low_score_samples": low_score_samples,
        "results": results,
    }

    if baseline_report_path is not None and baseline_report_path.exists():
        baseline = _load_json(baseline_report_path)
        if isinstance(baseline, dict):
            report["baseline_comparison"] = _compare_with_baseline(report, baseline)

    report_json_path = run_root / "report.json"
    report_md_path = run_root / "report.md"

    _write_json(report_json_path, report)
    _write_text(report_md_path, _render_markdown(report))

    print(f"done: {report_json_path}")
    print(f"done: {report_md_path}")
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline evaluation for video summarizer")
    parser.add_argument(
        "--dataset",
        default="evaluation/datasets/core_set.json",
        help="Path to evaluation dataset JSON",
    )
    parser.add_argument(
        "--output-dir",
        default="docs/evaluation/reports",
        help="Directory where reports will be stored",
    )
    parser.add_argument(
        "--run-id",
        default="",
        help="Run ID for report folder. Default: eval_YYYYMMDD_HHMMSS",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=None,
        help="Max enabled samples to run",
    )
    parser.add_argument(
        "--sample-ids",
        default="",
        help="Comma-separated sample IDs to run",
    )
    parser.add_argument(
        "--api-key",
        default="",
        help="OpenAI API key. If empty, uses OPENAI_API_KEY env var",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="OpenAI base URL. If empty, uses OPENAI_BASE_URL env var",
    )
    parser.add_argument(
        "--baseline-report",
        default="",
        help="Optional baseline report.json path for simple comparison",
    )
    parser.add_argument(
        "--judge-model",
        default="",
        help="Judge model name for LLM-as-a-judge evaluation. "
        "If empty, uses EVAL_JUDGE_MODEL env var, then OPENAI_MODEL_NAME env var, then gpt-4o. "
        "Use a cheaper model like gpt-4o-mini to reduce eval cost.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    dataset_path = (PROJECT_ROOT / args.dataset).resolve()
    output_dir = (PROJECT_ROOT / args.output_dir).resolve()

    if not dataset_path.exists():
        raise FileNotFoundError(f"dataset not found: {dataset_path}")

    run_id = args.run_id.strip() or f"eval_{_iso_now()}"
    api_key = args.api_key.strip() or os.getenv("OPENAI_API_KEY", "").strip()
    base_url = args.base_url.strip() or os.getenv("OPENAI_BASE_URL", "").strip()

    if not api_key:
        raise ValueError("OPENAI_API_KEY is required. Set env var or pass --api-key")

    sample_ids = [x.strip() for x in args.sample_ids.split(",") if x.strip()] if args.sample_ids else None
    baseline_report_path = None
    if args.baseline_report.strip():
        baseline_report_path = (PROJECT_ROOT / args.baseline_report).resolve()

    judge_model = args.judge_model.strip() or None

    run_eval(
        dataset_path=dataset_path,
        output_dir=output_dir,
        run_id=run_id,
        max_samples=args.max_samples,
        sample_ids=sample_ids,
        api_key=api_key,
        base_url=base_url or None,
        baseline_report_path=baseline_report_path,
        judge_model=judge_model,
    )


if __name__ == "__main__":
    main()
