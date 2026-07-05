# Offline Evaluation Report

- run_id: eval_20260508_153256
- generated_at: 2026-05-08T15:46:28.310989
- dataset_path: evaluation/datasets/core_set.json

## Overview

- total_samples: 6
- enabled_samples: 4
- success_samples: 4
- failed_samples: 0
- fact_avg_score: 0.3841
- task_avg_score: 0.9581
- fact_judge_confidence_avg: 0.9458
- task_judge_confidence_avg: 0.8875
- support_ratio_avg: 0.6879
- task_coverage_ratio_avg: 0.9581
- hallucination_density_avg: 0.3309
- weighted_hallucination_density_avg: 0.3038
- total_claim_importance: 279
- total_requirement_importance: 87
- total_entity_hallucinations: 0
- total_relation_action_hallucinations: 1
- total_fabrications: 23

## Low Score Samples

- eval-agent-context-2026-001: fact=fail(0.2326) task=pass(1.0) fact_confidence=0.8737 hallucination_density=0.4211 output=evaluation/reports/eval_20260508_153256/samples/eval-agent-context-2026-001/final_summary.md
- harness-engineering-agent-001: fact=fail(0.5) task=pass(0.9038) fact_confidence=0.9714 hallucination_density=0.2857 output=evaluation/reports/eval_20260508_153256/samples/harness-engineering-agent-001/final_summary.md
- video-summary-ai-transition-001: fact=fail(0.0938) task=pass(1.0) fact_confidence=0.955 hallucination_density=0.45 output=evaluation/reports/eval_20260508_153256/samples/video-summary-ai-transition-001/final_summary.md

## Per Sample Outputs

- eval-agent-context-2026-001: status=success fact=fail(0.2326) task=pass(1.0) fact_confidence=0.8737 task_confidence=0.925 hallucination_density=0.4211 output=evaluation/reports/eval_20260508_153256/samples/eval-agent-context-2026-001/final_summary.md
- video-rag-hallucination-001: status=success fact=warn(0.7101) task=pass(0.9286) fact_confidence=0.9833 task_confidence=0.86 hallucination_density=0.1667 output=evaluation/reports/eval_20260508_153256/samples/video-rag-hallucination-001/final_summary.md
- harness-engineering-agent-001: status=success fact=fail(0.5) task=pass(0.9038) fact_confidence=0.9714 task_confidence=0.875 hallucination_density=0.2857 output=evaluation/reports/eval_20260508_153256/samples/harness-engineering-agent-001/final_summary.md
- video-summary-ai-transition-001: status=success fact=fail(0.0938) task=pass(1.0) fact_confidence=0.955 task_confidence=0.89 hallucination_density=0.45 output=evaluation/reports/eval_20260508_153256/samples/video-summary-ai-transition-001/final_summary.md
