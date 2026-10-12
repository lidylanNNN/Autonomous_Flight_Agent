# MockHumanApproval

## 用途

测评和测试中按 Proposal ID 注入确定性的人工审批结果，不连接真实用户界面。
实现位于 `eval_harness/mocks/human_approval.py`，遵循
`HumanApprovalProtocol`，不随正式 `flight_agent` Python 包安装。

## 输入与输出

- 输入：`HumanApprovalRequest`，包含审批 ID、完整 `SkillProposal`、带时区的请求时间和正数超时秒数。
- 输出：`HumanApprovalResult`，包含审批 ID、Proposal ID、完整 Proposal 的 SHA-256 摘要和终态。
- 可注入终态：`APPROVED`、`REJECTED`、`TIMED_OUT`；未配置的 Proposal 默认超时。
- 审批结果只证明模拟的人工作出了决定，不代表 Safety 放行或飞行完成。

## 示例

```python
import asyncio

from eval_harness.mocks.human_approval import MockHumanApproval
from flight_agent.contracts import HumanApprovalStatus

approval = MockHumanApproval({'proposal-1': HumanApprovalStatus.REJECTED})
result = asyncio.run(approval.request_approval(request))
```

## 行为边界

- 每条请求对应独立状态机；审批 ID 或 Proposal ID 重复提交会报错。
- 请求入库时复制 Proposal；之后修改原对象不会改变审批快照和结果摘要。
- 批准或拒绝在请求时间立即模拟，超时在截止时间立即模拟；不等待真实时钟或人工回复。
- 无效事件、错误 Proposal 或终态重复转换不会获得批准；完整执行链在 M4-7 核对摘要和最新 Safety 检查。
- 此 Mock 不模拟认证、通信故障、真实超时调度或 Trace；评测场景 Fixture 和 Runner 在 M8 交付。
