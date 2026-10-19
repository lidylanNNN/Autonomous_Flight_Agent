# MX ROS-LLM 服务契约

本文件定义 MX-1 的 ROS 2 服务字段与跨节点语义。字段的可构建定义位于 [`rosllm_srvs`](../ros2_ws/src/rosllm_srvs/)；节点装配与调用顺序由 [SPEC_DEV.md 第 4 节](../SPEC_DEV.md)定义。服务类型存在不表示节点已实现或接入生产。

| 服务 | 请求 | 响应 | 失败与来源语义 |
|---|---|---|---|
| `Observation` | 空 | `bool success`, `string observation`, `string source`, `string message` | `success=false` 表示无法取得有效观察，`observation` 必须为空且 `message` 给出原因。成功时 `source` 必须标明真实状态或固定文本的来源；固定文本不能伪装成真实状态。 |
| `LLM` | `string prompt` | `bool success`, `string response`, `string info` | `success=false` 时 `response` 必须为空，`info` 给出原因，调用方不得派发行为。显式开发配置产生的固定回复必须在 `info` 标明来源。 |
| `ExecuteBehavior` | `string behavior` | `bool success`, `string message`, `int32 info` | `success` 表示序列执行结果，`message` 是面向人的摘要。`info` 保留上游信息标志字段；MX-1 不定义其数值枚举，调用方不得把它当成飞行诊断或 Safety 决策。 |
| `AtomicAction` | `string input`, `string prev_output` | `bool success`, `string output` | 具体动作由服务名选择。`input` 是动作参数文本，`prev_output` 是上一项输出；失败时 `output` 说明原因。飞行动作的成功只能由内部 Gate 和执行结果决定。 |

目标服务名分别为 `get_observation`、`llm`、`behavior_executor` 和各动作自己的服务名。具体名称与客户端连接在后续节点批次实现时验证。`Observation` 的文本和来源字段替代上游 `rosllm_msgs/Observation[]`；`LLM` 把上游 `LLMResponse` 中当前主链所需的成功、回复和信息字段直接置于服务响应，暂不传请求编号与时间戳。V1 不处理图像观察，因而不创建无实际使用者的 `rosllm_msgs` 包。`ExecuteBehavior` 和 `AtomicAction` 保持上游字段名称和类型。

上游字段依据：[Observation.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/Observation.srv)、[LLM.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/LLM.srv)、[ExecuteBehavior.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/ExecuteBehavior.srv)、[AtomicAction.srv](https://github.com/huawei-noah/HEBO/blob/master/ROSLLM/rosllm_srvs/srv/AtomicAction.srv)。正式上游对照仍须绑定准确 commit。
