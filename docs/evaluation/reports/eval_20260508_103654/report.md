# Offline Evaluation Report

- run_id: eval_20260508_103654
- generated_at: 2026-05-08T10:39:45.617547
- dataset_path: evaluation/datasets/core_set.json

## Overview

- total_samples: 3
- enabled_samples: 1
- success_samples: 1
- failed_samples: 0
- fact_avg_score: 0.1692
- task_avg_score: 1.0
- fact_judge_confidence_avg: 0.8526
- task_judge_confidence_avg: 0.925
- support_ratio_avg: 0.5641
- task_coverage_ratio_avg: 1.0
- hallucination_density_avg: 0.4737
- weighted_hallucination_density_avg: 0.3949
- total_claim_importance: 78
- total_requirement_importance: 20
- total_entity_hallucinations: 1
- total_relation_action_hallucinations: 0
- total_fabrications: 8

## Low Score Samples

- eval-agent-context-2026-001: fact=fail(0.1692) task=pass(1.0) fact_confidence=0.8526 hallucination_density=0.4737 output=evaluation/reports/eval_20260508_103654/samples/eval-agent-context-2026-001/final_summary.md

## Per Sample Outputs

- eval-agent-context-2026-001: status=success fact=fail(0.1692) task=pass(1.0) fact_confidence=0.8526 task_confidence=0.925 hallucination_density=0.4737 output=evaluation/reports/eval_20260508_103654/samples/eval-agent-context-2026-001/final_summary.md
