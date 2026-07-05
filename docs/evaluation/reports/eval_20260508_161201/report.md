# Offline Evaluation Report

- run_id: eval_20260508_161201
- generated_at: 2026-05-08T16:29:18.769113
- dataset_path: evaluation/datasets/core_set.json

## Overview

- total_samples: 6
- enabled_samples: 4
- success_samples: 4
- failed_samples: 0
- fact_avg_score: 0.6664
- task_avg_score: 0.9058
- fact_judge_confidence_avg: 0.8983
- task_judge_confidence_avg: 0.8604
- support_ratio_avg: 0.5816
- task_coverage_ratio_avg: 0.9138
- hallucination_density_avg: 0.4247
- weighted_hallucination_density_avg: 0.3837
- total_claim_importance: 312
- total_requirement_importance: 90
- total_entity_hallucinations: 3
- total_relation_action_hallucinations: 0
- total_fabrications: 28

## Low Score Samples

- video-summary-ai-transition-001: fact=fail(0.4142) task=pass(0.9835) fact_confidence=0.8781 hallucination_density=0.75 output=evaluation/reports/eval_20260508_161201/samples/video-summary-ai-transition-001/final_summary.md

## Per Sample Outputs

- eval-agent-context-2026-001: status=success fact=warn(0.653) task=pass(0.9888) fact_confidence=0.8591 task_confidence=0.925 hallucination_density=0.4545 output=evaluation/reports/eval_20260508_161201/samples/eval-agent-context-2026-001/final_summary.md
- video-rag-hallucination-001: status=success fact=pass(0.9615) task=pass(0.9347) fact_confidence=0.995 task_confidence=0.86 hallucination_density=0.05 output=evaluation/reports/eval_20260508_161201/samples/video-rag-hallucination-001/final_summary.md
- harness-engineering-agent-001: status=success fact=warn(0.6369) task=warn(0.7163) fact_confidence=0.8611 task_confidence=0.7667 hallucination_density=0.4444 output=evaluation/reports/eval_20260508_161201/samples/harness-engineering-agent-001/final_summary.md
- video-summary-ai-transition-001: status=success fact=fail(0.4142) task=pass(0.9835) fact_confidence=0.8781 task_confidence=0.89 hallucination_density=0.75 output=evaluation/reports/eval_20260508_161201/samples/video-summary-ai-transition-001/final_summary.md
