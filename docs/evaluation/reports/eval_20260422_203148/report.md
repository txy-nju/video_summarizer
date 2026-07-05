# Offline Evaluation Report

- run_id: eval_20260422_203148
- generated_at: 2026-04-22T20:35:19.743264
- dataset_path: evaluation/datasets/core_set.json

## Overview

- total_samples: 3
- enabled_samples: 1
- success_samples: 1
- failed_samples: 0
- fact_avg_score: 0.0
- task_avg_score: 1.0
- fact_judge_confidence_avg: 0.9481
- task_judge_confidence_avg: 0.925
- support_ratio_avg: 0.4274
- task_coverage_ratio_avg: 1.0
- hallucination_density_avg: 0.5926
- weighted_hallucination_density_avg: 0.4496
- total_claim_importance: 117
- total_requirement_importance: 20
- total_entity_hallucinations: 4
- total_relation_action_hallucinations: 0
- total_fabrications: 12

## Low Score Samples

- eval-agent-context-2026-001: fact=fail(0.0) task=pass(1.0) fact_confidence=0.9481 hallucination_density=0.5926 output=evaluation/reports/eval_20260422_203148/samples/eval-agent-context-2026-001/final_summary.md

## Per Sample Outputs

- eval-agent-context-2026-001: status=success fact=fail(0.0) task=pass(1.0) fact_confidence=0.9481 task_confidence=0.925 hallucination_density=0.5926 output=evaluation/reports/eval_20260422_203148/samples/eval-agent-context-2026-001/final_summary.md
