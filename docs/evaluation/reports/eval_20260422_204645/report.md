# Offline Evaluation Report

- run_id: eval_20260422_204645
- generated_at: 2026-04-22T20:50:15.119846
- dataset_path: evaluation/datasets/core_set.json

## Overview

- total_samples: 3
- enabled_samples: 1
- success_samples: 1
- failed_samples: 0
- fact_avg_score: 0.2842
- task_avg_score: 1.0
- fact_judge_confidence_avg: 0.9462
- task_judge_confidence_avg: 0.925
- support_ratio_avg: 0.5789
- task_coverage_ratio_avg: 1.0
- hallucination_density_avg: 0.4615
- weighted_hallucination_density_avg: 0.2947
- total_claim_importance: 57
- total_requirement_importance: 20
- total_entity_hallucinations: 1
- total_relation_action_hallucinations: 2
- total_fabrications: 3

## Low Score Samples

- eval-agent-context-2026-001: fact=fail(0.2842) task=pass(1.0) fact_confidence=0.9462 hallucination_density=0.4615 output=evaluation/reports/eval_20260422_204645/samples/eval-agent-context-2026-001/final_summary.md

## Per Sample Outputs

- eval-agent-context-2026-001: status=success fact=fail(0.2842) task=pass(1.0) fact_confidence=0.9462 task_confidence=0.925 hallucination_density=0.4615 output=evaluation/reports/eval_20260422_204645/samples/eval-agent-context-2026-001/final_summary.md
