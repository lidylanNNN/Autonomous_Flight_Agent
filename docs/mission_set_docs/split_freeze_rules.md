# 测评集拆分与冻结规则

## 目的

本文档定义 Mission Case 如何划分到 `dev`、`validation` 和 `frozen_test`。

本项目中的 `split` 表示用于特定 Evaluation 阶段的一组测评任务。

## Split 用途

| Split | 用途 | 可见性 | 是否可以据此调参 |
|---|---|---|---|
| `dev` | 日常开发、Debug、Regression Check | 完全可见 | 可以 |
| `validation` | 里程碑级回归和设计比较 | 创建后可见 | 有限；禁止 Case-specific 特殊处理 |
| `frozen_test` | 最终报告与求职级指标 | 只开放冻结 Manifest 和结果 | 不可以 |

## Family-level 规则

最小 Split 单位是 Task Family，而不是单个 Case。

例如：

```text
DEV-T01-N
DEV-T01-C
DEV-T01-F
```

这三条属于同一个 Family，必须处于同一个 Split。来自同一 Family 的 Normal、
Constraint Boundary 和 Fault Disturbance Case 不得拆到不同 Split。

该规则与 Group-based Dataset Split 原则一致：高度相关的 Sample 不得在开发组与测试组
之间泄漏。

## 冻结规则

`frozen_test` 不是调参集。冻结后：

- 不得原地修改冻结 Case 文件。
- 不得针对单条冻结 Case 调整 Prompt、Safety Rule、Recovery Policy 或 Grading Logic。
- 不得添加 Case ID 专用分支来通过已知冻结 Case。
- Run 未记录 Source Commit、Environment Manifest、Model/Provider Version 和 Eval
  Harness Version 时，不得报告 Frozen Result。
- 冻结 Case 无效时，应创建新 Split Version 并记录原因，不得静默修改旧版本。

## Manifest 必需字段

每个 Split Manifest 应记录：

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

字段含义：

- `source_commit`：创建或冻结 Split 时使用的 Git Commit Hash。
- `random_seed`：随机分组的固定起点；仅当 `split_method` 为
  `family_level_random_split` 时必须填写，用于保证同一批 Task Family 未来仍能得到
  相同 Split。M0 的 `manual_family_level` Split 保留为 `null`。
- `status`：Split 完成前为 `draft`；成为可报告 Benchmark 后为 `frozen`。

## M0 Policy

M0 定义 Split Contract，并预留 `validation` 和 `frozen_test` Manifest。

实际 Validation 与 Frozen Case 应在第一版可运行 PX4/Gazebo Evaluation Loop 建成后
填充，避免在 Runtime 和 Grading 路径确定前过早冻结最终测评集。
