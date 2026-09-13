# Mission Set Split And Freeze Rules

## Purpose

This document defines how mission cases are divided into `dev`, `validation`, and
`frozen_test` splits.

In this project, `split` means a mission-set group used for a specific evaluation
stage.

## Split Roles

| Split | Use | Visibility | Can tune against it |
|---|---|---|---|
| `dev` | Daily development, debugging, regression checks | Fully visible | Yes |
| `validation` | Milestone-level regression and design comparison | Visible after creation | Limited; no case-specific special handling |
| `frozen_test` | Final report and portfolio-grade metrics | Frozen manifest and results only | No |

## Family-Level Rule

The minimum split unit is the task family, not a single case.

Example:

```text
DEV-T01-N
DEV-T01-C
DEV-T01-F
```

These three cases are one family. They must stay in the same split. A normal
case, constraint-boundary case, and fault-disturbance case from the same family
must not be split across `dev`, `validation`, and `frozen_test`.

This follows the same principle as group-based dataset splitting: closely related
samples must not leak across development and test groups.

## Freeze Rules

`frozen_test` is not a tuning set.

After a frozen split is created:

- Do not edit frozen case files in place.
- Do not tune prompts, safety rules, recovery policy, or grading logic against
  individual frozen cases.
- Do not add case-id-specific branches to pass known frozen cases.
- Do not report frozen results unless the run records the source commit,
  environment manifest, model/provider version, and eval harness version.
- If a frozen case is invalid, create a new split version and document the reason
  instead of silently editing the old one.

## Required Manifest Fields

Each split manifest should record:

```text
manifest_id
spec_version
schema
split
status
split_method
source_commit
random_seed
environment_manifest
case_count
family_count
family_ids
case_files
notes
```

Field meanings:

- `source_commit`: Git commit hash used when the split was created or frozen.
- `random_seed`: 随机分组的固定起点；用于保证同一批任务族在未来可以被重复分到相同的 split。
- `status`: `draft` before the split is final, `frozen` after it becomes a
  reportable benchmark split.

## M0 Policy

M0 defines the split contract and reserves `validation` / `frozen_test` manifests.

Actual validation and frozen cases should be populated after the first runnable
PX4/Gazebo evaluation loop exists, so that final splits are not frozen before the
runtime and grading path are known.
