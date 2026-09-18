# ADR-001 — 已批准 Skill 的 Runtime 边界

状态：已接受

## 背景

M3 需要在 M4 Safety Supervisor 尚未实现时先确定 Runtime 接口。如果允许
Runtime 直接接收原始 Proposal，后续 Agent 或测试代码就可能意外绕过 Safety。
另外，异步执行开始前必须已有稳定标识，取消请求才能准确找到对应任务。

## 候选方案

1. Runtime 接收原始 `SkillProposal`，并在内部执行安全检查。
2. Runtime 接收松散的 `skill_name` 和参数字典。
3. Runtime 只接收强类型 `ApprovedSkillCommand`，其中携带 Proposal、Decision、
   State、Execution、Timeout 和强类型参数信息。

## 决策

采用方案 3。`FlightExecutionInterface.execute()` 只接收 `ApprovedSkillCommand`。
Safety Policy 不放进单个 Skill 或 Runtime Adapter。M3 阶段允许脚本化测试显式
构造已批准命令；M4 实现后，生产链路只能由 Safety 审批结果创建该命令。

## 原因

- 让 Safety 到 Runtime 的边界明确且可审计。
- 保证 Mock Runtime 与 PX4 Runtime 可以互换。
- 防止 ROS/PX4 细节进入业务 Contract。
- 预先分配 `execution_id`，为超时和取消提供稳定标识。
- 保留 Proposal 与 Decision 标识，方便后续 Trace 关联。

## 影响

- M3 Fixture 必须包含合成的审批标识。
- M4 只能在确定性审批通过后创建该命令。
- Runtime 会在下发前拒绝格式错误或无类型约束的参数。
- PX4 高层导航与 Offboard 的实际执行路线仍是独立决策，真实 Adapter 开发前
  需要再写一份 ADR。

## 依据

- `DEV_SPEC.md` 第 7.7、8.2、9、21.8.5 和 21.8.7 节。
- `tests/contract/skills/` 下的 Contract Test。
