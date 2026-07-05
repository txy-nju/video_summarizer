# Offline Evaluation Report

- run_id: eval_20260427_192837
- generated_at: 2026-04-27T19:31:46.907585
- dataset_path: evaluation/datasets/core_set.json

## Overview

- total_samples: 3
- enabled_samples: 1
- success_samples: 1
- failed_samples: 0
- fact_avg_score: 0.4298
- task_avg_score: 1.0
- fact_judge_confidence_avg: 0.9682
- task_judge_confidence_avg: 0.9083
- support_ratio_avg: 0.6809
- task_coverage_ratio_avg: 1.0
- hallucination_density_avg: 0.3636
- weighted_hallucination_density_avg: 0.2511
- total_claim_importance: 47
- total_requirement_importance: 28
- total_entity_hallucinations: 1
- total_relation_action_hallucinations: 0
- total_fabrications: 3

## Low Score Samples

- eval-agent-context-2026-001: fact=fail(0.4298) task=pass(1.0) fact_confidence=0.9682 hallucination_density=0.3636 output=evaluation/reports/eval_20260427_192837/samples/eval-agent-context-2026-001/final_summary.md

## Per Sample Outputs

- eval-agent-context-2026-001: status=success fact=fail(0.4298) task=pass(1.0) fact_confidence=0.9682 task_confidence=0.9083 hallucination_density=0.3636 output=evaluation/reports/eval_20260427_192837/samples/eval-agent-context-2026-001/final_summary.md
