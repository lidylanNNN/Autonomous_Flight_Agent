# Autonomous Flight Agent 开发规格 v2.1

> 状态：当前总体设计基线；待确认事项见第 10 节。原实现基线：2026-10-16 的 `8a13b81`；MX 后续进度见 [MX 计划](plans/MX.md)。架构对齐依据为 ROS-LLM 论文 v3 与第 1.2 节固定的开源版本。设计目标不代表代码迁移已完成。
> 历史规格：[DEV_SPEC v1.44](docs/archive/DEV_SPEC_v1.44.md)。历史文件保留决策过程，不再作为当前实现或新开发的验收依据。
> 适用范围：V1 飞行机器人任务系统、仓库结构、开发顺序、测评和版本管理。代码事实以实际生产入口及测试证据为准；本文目标不自动等于已实现。

## 1. 项目定义

本项目探索把自然语言任务变成可审查的飞行动作，并在 PX4/Gazebo 中验证执行结果。主编排以 ROS-LLM 为基线；ROS Noetic 到 ROS 2 Jazzy、机械臂到 PX4 飞行器的实现替换不得改变其调用顺序。具体目标流程见第 4 章。

目标使用场景是临时点位任务：操作员描述要去的位置和观察目的，系统选择受限动作、执行并返回可核对的飞行状态。相对手动逐条操控或预先制作固定航线，潜在收益是减少任务准备和重复操作；**节省的人力、时间与成本尚无实测，不能在对外材料中写成已取得的效果**。

系统边界：LLM 只负责任务理解与高层动作选择；确定性 Safety 决定物理动作是否允许；动作服务根据飞行状态判断动作是否完成；ROS 2 负责通信与适配；PX4 保持姿态、导航控制和原生 failsafe。Agent 不输出电机、推力、角速率或姿态闭环指令，不替代飞控固件。V1 不包含真实飞机测试、多机协同或适航声明；视觉及其他新增场景的范围与实施顺序以[新 M6 计划](plans/M6.md)为准，目标不代表已接入。

**V1 职责与评估重点**：本项目承担 Agent 开发工程师职责，负责工具选择、参数构造、调用顺序、结果传递、失败/超时处理及如实报告，并完成必要的接口适配和运行证据记录。视觉识别准确率、建图精度等工具业务效果由业务对接方负责提供并满足要求；V1 不训练、微调或优化工具算法。系统评测以真实目标完成率、未见/扰动完成率、耗时/成本、安全及相对冻结基线差值为主输出；工具调用与业务效果用于失败归因，口径见[测评设计](docs/eval_doc/v1_evaluation.md#评分口径)。现有 Safety 检查、飞行动作完成判定及真实结果记录继续执行，工具问题不能被报告成任务成功或从完成率分母排除。

设计依据分三层，不混称为同一技术方案：

1. **系统调用流**：以 [ROS-LLM 开源项目](https://github.com/huawei-noah/HEBO/tree/1573874e81e97213761a67950130e006bf13cf6d/ROSLLM)的 Agent、Observation Manager、Behavior Executor、Atomic Action Library 及人工反馈闭环为基线。不能把本项目自行设计的 Hooks、MissionService 或无人续跑称为上游架构。
2. **产品交互决策点**：参考 [DJI FlightHub 2 Copilot 公开手册](https://fh.dji.com/user-manual/cn/copilot-assistant/copilot-remote-control.html)讨论计划确认、接管和紧急停止；这不代表知道 DJI 的内部实现，也不把尚未实现的交互写成当前能力。
3. **飞行行为与安全条件**：以冻结的 PX4/ROS 2 接口、本仓库代码及 SITL 实测为准。产品参照不得覆盖确定性安全边界或 PX4 原生 failsafe。

### 1.1 V1 目标状态与对齐范围

V1 目标：取得初始观察后等待人工输入。`RosAgent` 根据观察和动作库生成行为序列；`SequenceExecutor` 逐项执行，失败即停；系统返回结果并更新观察，再等待人工反馈。任务成功须有状态证据，整条链须能在 PX4/Gazebo 测评。

**主调用拓扑与 ROS-LLM 相同**；V1 先验收 JSON 动作序列。行为树、状态机、代码执行和示教扩库未实现前，不声称全功能等价。V1 不包含无人自动续跑。

飞行 Gate、PX4 Backend 和在途监测是**飞行适配扩展**，不是 ROS-LLM 原生模块；它们不改变主调用拓扑。

### 1.2 源码证据与一一映射

工程参考版本固定为 [huawei-noah/HEBO `1573874`](https://github.com/huawei-noah/HEBO/commit/1573874e81e97213761a67950130e006bf13cf6d)，完整 SHA 为 `1573874e81e97213761a67950130e006bf13cf6d`，提交时间 2025-11-27 08:46:42 UTC，范围为 `ROSLLM/`。该版本晚于论文 v3，不代表论文实验使用的确切代码或模型权重；版本核对和差异证据见 [M5-2a-1](plans/M5.md#2a-1-实施记录)。

| ROS-LLM 依据 | 上游职责 | 本项目目标对应物 |
|---|---|---|
| [`ros_agent_srv` 主循环](https://github.com/huawei-noah/HEBO/blob/1573874e81e97213761a67950130e006bf13cf6d/ROSLLM/agent_comm/scripts/ros_agent_srv) | `main()` 创建 `RosEnv` 与 `RosAgent`，先调用 `env.get_obs()`，等待人工输入，再调用 `agent.get_action(human, obs)` 和 `env.step(action)`；`step()` 内执行行为并再次获取观察 | 唯一的有人任务入口；主循环调度 `RosAgent` 决策和 `RosEnv` 环境交互，二者互不调度 |
| [`agent_comm`](https://github.com/huawei-noah/HEBO/tree/1573874e81e97213761a67950130e006bf13cf6d/ROSLLM/agent_comm) | LLM 服务与 Agent 通信 | LLM Provider 和 prompt/行为提取 |
| [`observation_manager`](https://github.com/huawei-noah/HEBO/tree/1573874e81e97213761a67950130e006bf13cf6d/ROSLLM/observation_manager) | 聚合各观察服务，通过统一 `get_observation` 返回文本观察 | 由 `WorldState` 等来源构造统一观察，不让 Agent 直接消费 PX4 Topic |
| [`behavior_executor`](https://github.com/huawei-noah/HEBO/tree/1573874e81e97213761a67950130e006bf13cf6d/ROSLLM/behavior_executor) | `ExecuteBehavior` 服务接收行为；序列/行为树/代码等执行器返回成功标志与消息 | ROS 2 行为执行器；V1 先实现序列模式，逐项调用原子动作、失败即停止 |
| [论文 §3.1、§3.5](https://arxiv.org/html/2406.19741v3) | 带名称、类型、描述和输入/输出的原子动作库；JSON 序列中的动作依次执行，前项输出可传给后项 | 飞行原子动作描述库及 ROS 2 action/service 接口，内部复用 Gate/Backend |
| [论文 §3.2–§3.4](https://arxiv.org/html/2406.19741v3) | 文本观察、人工反馈和动作库共同进入每轮 prompt；一轮行为结束后再等待人 | 飞行状态文本化、人工下一轮反馈 |

固定版本的 prompt 与执行器均支持 JSON Lines 多动作序列。原规格“上游正则只捕获首个 JSON 对象”的判断不成立：该正则受结束代码围栏约束，可以捕获完整多行对象。本项目的区别是使用整段匹配及派发前完整序列校验，M5 继续完善真实模型输出的边界处理；不能将已验证的两步提取归因为修复了上述虚构缺口。

上游以搜索方式取首个匹配代码块，本项目拒绝块外说明文字；当前本项目提取器仍可能合并多个代码块，再由执行器解析拒绝。该边界尚未在提取层收紧，不将目标中的“唯一代码块”当作已完整实现。

## 2. 当前实现基线

下表描述仓库当前状态，不是目标架构图。`plans/M0.md` 至 `M4.md` 保存阶段记录；M5 计划与分批证据见 [M5](plans/M5.md)，后续未建立的里程碑文件不能当作现存交付物。

| 能力 | 当前代码/入口 | 可声称的边界 |
|---|---|---|
| 环境与状态 | `ros2_ws/`、`scripts/ros2_px4/`、`Px4WorldStateAdapter`、`WorldStateAggregator` | M1/M2 的 PX4/Gazebo、ROS 2 消息和状态聚合基础已建立；运行环境以 `manifests/environment.yaml` 为准 |
| MX-3 有人入口与行为序列 | `ros_agent_srv.main()` 已装配 `RosEnv`、`RosAgent`、观察服务和行为执行器；逐边调用见第 4 节 | M5 已接入实际观察摘要、五动作说明、真实 Flash 调用、预算保护及终端诊断，并取得独立进程下真实模型起降、多动作及超高拒绝的受控 SITL 证据；范围和遗留问题见 [M5 收口审查](plans/M5.md#4c-收口审查)。下一轮 prompt 尚无上一轮执行诊断 |
| MX-4 飞行动作服务 | `flight_atomic_action_server` 从 `--safety-json` 加载限制，发布 `hold`、`takeoff`、`goto`、`rtl`、`land` 并调用 Safety Gate | 五个服务测试覆盖 `UNKNOWN` 拒绝、Trace/状态读取失败零派发，以及替身批准、执行失败和异常响应；不代表 PX4/Gazebo 端到端证据 |
| 确定性飞行动作 | `flight_execution_backend.py`、PX4 Command/Offboard adapters、Skill Registry | Takeoff、GoTo、Hold、RTL、Land 有代码映射；M3 调试脚本有 SITL 验证记录，不等于 Agent 任务闭环 |
| 执行前 Safety | `SafetyExecutionGate` 调用 `SafetySupervisor`，批准后调用 `FlightExecutionBackend` | Gate 和检查器、决策 Trace 已实现；MX-4 1b 已移除 Proposal 与 Gate 的旧任务身份；ROS 侧 main 已按 M4-7c 设置默认权限并验证启动传参与日志；Takeoff/Land 的真实服务起降及 Hold 5 秒已通过，GoTo、RTL 已通过受控场景验证；见 [7f 记录](plans/M4.md#7f-实施记录)、[7g-1 记录](plans/M4.md#7g-1-实施记录)、[7g-2 记录](plans/M4.md#7g-2-实施记录)与 [7g-3 记录](plans/M4.md#7g-3-实施记录) |
| 动作完成判定 | 飞行动作服务复用 PX4 Backend 的状态完成条件 | 服务等待执行结果，Safety 批准且执行状态为 `SUCCEEDED` 才返回成功；序列执行器收到成功后继续，失败即停 |
| Trace | `TraceRecorder`、`TraceReplay` | 已接入 WorldState、安全决策、观察、LLM 响应及 Gate 动作结果/异常记录，并通过替身主链审计，详见 [MX-5 审计](plans/MX-5-audit.md)；完整 Episode 因果链尚未完成 |
| 测评数据 | `agent_benchmark_sets/`、schema 与 validation scripts | Dev 用例存在；Validation/Frozen Test 目前只是预留 manifest，不得报告冻结测评指标 |
| 拍照 | `capture_photo_server`、共享动作白名单及 `RosEnv` 动作说明 | 地面接入及交付见 [M6 T03–T04](plans/M6.md)；固定单点及两点飞行、拍照检测与返航交付已按 [T05a/T05b](plans/M6.md#t05b-分级新鲜度实施与两点复验2026-11-18)验证，不代表系统性能 |
| 图片检测 | `inspect_image_server`、共享动作白名单及 Registry 描述 | 独立服务及地面串联见 [T04b/T04c](plans/M6.md#t04c-agent-拍照后检测2026-11-18)；固定两点飞行视角检测与返航交付见 [T05b](plans/M6.md#t05b-分级新鲜度实施与两点复验2026-11-18)，固定空场景真实未检出及交付见 [T06a](plans/M6.md#t06a-无目标飞行视觉验证2026-11-18)，不代表检测器一般准确率 |
| 旧审批原型 | 模型、协议、组件、Mock、公共导出和专属测试已删除 | 旧审批决策枚举与原因码也已移除；见 [删除记录](plans/M4.md#8b-18b-2-删除范围与影响)与 [契约收敛](plans/M4.md#8b-3-契约收敛) |

### 2.1 文件处置决定（分批实施）

| 现有文件/组 | 决定 | 实施条件 |
|---|---|---|
| `src/flight_agent/workflows/mission/service.py`、其 `__init__.py` 导出 | **已删除** | MX-4 6a-2 已移除固定语句规划与旧恢复循环，见[实施记录](plans/MX-4-structure-change.md#6a-2-实施记录删除旧入口与任务流程) |
| `MissionContract`、`mission_flow_model.py` 及相关公共导出 | **已删除** | 6b 已核对 `PlanStep`、`VerificationResult` 无生产使用者并一并删除；保留 `MissionConstraints` 和 `NedGeofence` |
| `mission_cli.py`、ROS 包的 `setup.py` | **旧 CLI 及注册已删除** | 独立 M3 调试入口保留；有人入口使用 `ros_agent_srv.main()` |
| `tests/integration/test_mission_flow.py`、ROS 包的 `test_mission_cli.py` | **已删除，M3 测试已迁出** | `test_flight_round.py` 覆盖新主链；`test_m3_scripted_flight.py` 保留独立调试回归 |
| `components/skills/registry.py`、`skill_model.py` | **保留并扩展** | 动作定义补齐名称、类型、描述、输入/输出，供 prompt 与执行器共用 |
| `components/vehicle/state/`、`Px4WorldStateAdapter` | **保留并接入观察层** | 新建统一观察入口；行为执行后再次读取 |
| `components/safety/`、`safety_model.py`、PX4 Backend/adapters | **保留并复用** | 飞行原子动作经 Gate 调用 Supervisor；只有批准命令才交给 Backend。逐边数据见第 4 节 |
| `components/tracing/`、`agent_benchmark_sets/`、`eval_harness/` | **保留并扩展** | 提供审计与测评，不参与任务决策 |
| 旧 `human_approval` 组件、模型、协议、Mock、公共导出及专属测试 | **已删除** | [M4-8b-1/2](plans/M4.md#8b-18b-2-删除范围与影响) 已移除未接入的原型；旧决策枚举与原因码已按 [8b-3](plans/M4.md#8b-3-契约收敛) 清理，未来产品审批另行设计 |
| 旧设计中的通用 `PreToolUse`/`PostToolUse`/`OnToolError`/`Stop` Hooks 与第二套 Tool Invocation 工作流 | **从目标架构删除** | 当前无对应生产模块；记录与超时由 Behavior Executor 或飞行原子动作处理，不新增第二套任务调度。V1 暂不实现取消协议 |

`configs/safety.yaml` 仍是未生效的预留配置。MX-4 另建安全 JSON，由 `--safety-json` 加载系统 `MissionConstraints`；两者不能混作已生效来源。表中未完成项仍按实施条件分批迁移。

**实际入口必须分开报告：**

- 有人入口 `ros_agent_srv` 通过 ROS 服务调用观察、LLM 和行为执行器；旧 `mission_cli` 及其任务流程已删除。
- M3 独立调试入口 `m3_scripted_flight` 直接调用 PX4 ROS 2 Backend，显式绕过 Gate；只作 M3 调试与回归，不进入自主安全测评。`run_scripted_sitl_flight.sh` 已改用新入口。
- MX-4 飞行动作入口 `flight_atomic_action_server --safety-json <路径>` 目前发布 `hold`、`takeoff`、`goto`、`rtl`、`land`；生产 Authority 固定 `AGENT_ALLOWED`，其余检查通过且决策记录成功后才能调用 Backend；验证范围见 [7c 记录](plans/M4.md#7c-实施记录)。

## 3. 固定环境与仓库边界

`manifests/environment.yaml` 固定 Ubuntu 24.04、Python 3.12/uv、ROS 2 Jazzy、PX4 v1.16.2 对应提交、与其匹配的内嵌 `px4_msgs`、Gazebo Harmonic、x500 和 uXRCE-DDS。`pyproject.toml` 与 `uv.lock` 管理核心 Python 环境；ROS 包通过 `ros2_ws`/colcon 构建。Docker/Compose 仍是计划，不是当前部署证据。环境 manifest 中较早的 `spec_version` 是历史字段，更新前不得把它当成本规格版本。

当前仓库的职责分界：

```text
src/flight_agent/contracts/          Pydantic 数据契约、Protocol、错误类型
src/flight_agent/components/         Safety、Skill Registry、Mock Backend、State、Trace
ros2_ws/src/flight_agent_ros/        ROS 2 入口、PX4 状态/命令/参数/Offboard 适配
ros2_ws/src/px4_msgs/                与冻结 PX4 版本匹配的消息定义
agent_benchmark_sets/               Agent 测评数据与 schema，不是在线任务或 PX4 Mission
eval_harness/                        测评辅助代码；完整 Episode Runner 尚未实现
tests/                               单元、契约和替身集成测试
AGENT.md、SPEC_DEV.md                 长期规则与当前总体设计
contracts/、modules/                 详细接口契约与复杂模块内部设计（区别于代码包）
plans/、ADR/                          阶段计划与重要设计决策
docs/                                历史归档、环境操作手册、测评辅助文档
manifests/、configs/                 环境声明与配置；预留配置不自动生效
```

核心代码不得直接依赖 `rclpy`、`px4_msgs` 或 ROS Topic 名；PX4 适配留在 ROS 包。`flight_agent` 不依赖 `eval_harness`，测评集不反向依赖产品代码。目录存在不等于能力已接入；新增目录应由实际生产调用链驱动，不先批量铺设空壳。

### 3.1 目标目录与文件

以下是**迁移完成后的目标路径，不是当前文件清单**。主链采用 [ROS-LLM 源码的包名](https://github.com/huawei-noah/HEBO/tree/1573874e81e97213761a67950130e006bf13cf6d/ROSLLM)：`agent_comm`、`observation_manager`、`behavior_executor`、`rosllm_srvs`、`rosllm_msgs`。节点沿用上游的 `ros_agent_srv`、`llm_node`、`observation_manager_node`、`sequence_executor`；`.py`、`setup.py`、`package.xml` 和接口构建方式按 ROS 2 调整。`flight_agent_ros`、`px4_msgs`、`flight_agent` 是 PX4/飞行适配或现有 Python 工程边界，不冒充上游包。没有自定义消息需求前不创建空的 `rosllm_msgs` 包。

```text
ros2_ws/src/
  agent_comm/                          [MX-2 已建；上游同名 ROS 包]
    package.xml
    setup.py
    config/
      atomic_action_library.yaml      [五动作索引；1b 由 RosEnv 生成输入/输出说明]
    agent_comm/
      ros_agent_srv.py                 [MX-3 已接入行为执行与再观察]
      llm_node.py                      [Flash HTTP 接线已实现；保留显式固定模式]
  observation_manager/                [MX-2 已建；上游同名 ROS 包]
    package.xml
    setup.py
    observation_manager/
      observation_manager_node.py      [M5-1a 已输出实际 WorldState 摘要]
      vehicle_state_summarizer.py      [未建；1a 在现有回调内摘要，暂无独立模块需求]
  behavior_executor/                  [MX-3 已建；上游同名 ROS 包]
    package.xml
    setup.py
    behavior_executor/
      sequence_executor.py            [已建；ExecuteBehavior 序列执行，失败停止]
  rosllm_srvs/                         [已建；四个服务接口供当前 ROS 节点引用]
    package.xml
    CMakeLists.txt
    srv/
      AtomicAction.srv                 [已定义；原子动作契约]
      ExecuteBehavior.srv             [已定义；行为执行契约]
      Observation.srv                 [已定义；统一观察契约]
      LLM.srv                         [已定义；模型调用契约]
  rosllm_msgs/                         [V1 无自定义消息时不创建]
  flight_agent_ros/                    [已存在；PX4/ROS 2 飞行适配]
    flight_agent_ros/
      flight_atomic_action_server.py  [已建；目前发布五个飞行动作，经 Safety Gate]
      adapters/
        px4_world_state_adapter.py      [已实现；观察源，不等于 Observation Manager]
        px4_parameter_adapter.py        [已实现；飞行参数读取]
        flight_execution_backend.py     [已实现；动作内调用，不等于 Behavior Executor]
        px4_command_plan_mapper.py      [已实现；PX4 命令映射]
        px4_command_plan_executor.py    [已实现；PX4 命令执行，不等于 sequence_executor]
        px4_vehicle_command_adapter.py  [已实现；PX4 命令与 ACK]
        px4_offboard_setpoint_adapter.py [已实现；PX4 Offboard]
  px4_msgs/                            [已存在；冻结 PX4 消息定义]
src/flight_agent/
  contracts/                           [已存在；Pydantic 契约与 Protocol]
  components/
    vehicle/state/world_state_aggregator.py [已实现；状态聚合]
    safety/                            [已实现；飞行原子动作内部 Gate/检查]
    skills/registry.py                 [已有参数 schema；1b 供 RosEnv 生成服务描述]
    tracing/                           [已实现；追踪基础]
agent_benchmark_sets/                 [已存在；Agent 测评集]
eval_harness/                         [已存在；测评执行辅助]
tests/                                [已存在；迁移后新增主链集成测试]
```

上游的 `behavior_tree_executor`、`code_executor`、视觉节点、示例包及 `GetUserInput.srv` 不进入 V1 文件树；有人入口先沿用上游 `ros_agent_srv` 的终端输入方式。V1 只实现序列模式。上述路径固定职责和迁移目标，**不表示已复制或实现上游代码**。`rosllm_srvs` 的字段与失败语义见 [服务契约](contracts/rosllm_services.md)；接口构建证据见 [MX 记录](plans/MX.md)。

现有模块的保留、复用和迁移决定见第 2.1 节。命名须反映实际职责：PX4 命令执行器不是行为序列执行器，状态聚合器不是 Observation Manager；不能为凑同名把已有模块改称尚未实现的上游模块。

## 4. ROS-LLM 对齐的目标调用链

现有主链采用 ROS-LLM 的一个环境步：`ros_agent_srv.main()` 先调用 `RosEnv.get_obs()`，等待人工输入，调用 `RosAgent.get_action(human, obs)` 并提取行为，再调用 `RosEnv.step(action)`；`step()` 调用 Behavior Executor 执行行为并再次获取观察。下一轮仍由 `main()` 等待人输入。`RosEnv` 不调用 `RosAgent`，LLM 不在每个原子动作后自动重规划，执行器或 Runtime 也不建立另一套任务循环。该拓扑已按 [M5](plans/M5.md#4c-收口审查)验证，下面的视觉/位置服务示例仍是目标链路。

下方示例逐边标明模块间传递的数据。飞行动作内部的 Safety、Backend 和 Trace 边见 [MX-4 调用流](plans/MX-4-structure-change.md#调用流)。动作完成判定由飞行动作服务负责，复用 Backend 的状态判断，不设置独立 Verification 节点。运行中安全监测的缺口归 M7-S，不能用已有 PX4 failsafe 代替；这些安全路径不选择下一行为。

一次完整转换示例：用户说“去三号塔看看有没有异物，拍张照，然后回来”。此例**假设飞机已在空中**，三号塔位置库、相机和识别服务已经接入；三号塔位置解析与异物识别尚未实现，不能把完整示例当作 V1 已有能力；已接入的拍照与人物检测见第 2 节。方括号内是模块、配置源或外部参与者，箭头上是输出的数据对象；`RosAgent`、`SequenceExecutor`、`SafetyExecutionGate` 等是对应模块持有的类对象，不是链路中的数据对象。以下为目标链路，标为“待建”的模块尚未接入生产。

```text
[agent_comm.config.atomic_action_library.yaml] --AtomicActionLibrary(动作描述)--> [agent_comm.RosEnv]
[agent_comm.RosEnv] --AtomicActionLibrary--> [agent_comm.RosAgent(由 ros_agent_srv.main 创建)]
[agent_comm.ros_agent_srv.main] --get_obs()--> [agent_comm.RosEnv]
[agent_comm.RosEnv] --ObservationRequest--> [observation_manager.observation_manager_node]
[observation_manager.observation_manager_node] --ObservationResponse(当前观察)--> [agent_comm.RosEnv]
[agent_comm.RosEnv] --当前观察--> [agent_comm.ros_agent_srv.main]
[操作员] --HumanInput("去三号塔看看有没有异物，拍张照，然后回来")--> [agent_comm.ros_agent_srv.main]
[agent_comm.ros_agent_srv.main] --HumanInput + 当前观察--> [agent_comm.RosAgent.get_action]
[agent_comm.RosAgent.get_action] --LLMRequest(prompt)--> [agent_comm.llm_node]
[agent_comm.llm_node] --LLMResponse(逐行 JSON 行为文本)--> [agent_comm.RosAgent.get_action]
[agent_comm.RosAgent.get_action] --行为文本--> [agent_comm.ros_agent_srv.main]
[agent_comm.ros_agent_srv.main] --行为文本--> [agent_comm.RosEnv.step]
[agent_comm.RosEnv.step] --ExecuteBehaviorRequest(behavior=行为文本)--> [behavior_executor.sequence_executor]

[behavior_executor.sequence_executor] --AtomicActionRequest(input="三号塔", prev_output="")--> [外部 resolve_poi 服务；未实现]
[外部 resolve_poi 服务；未实现] --AtomicActionResponse(output=目标位置)--> [behavior_executor.sequence_executor]
[behavior_executor.sequence_executor] --AtomicActionRequest(input=飞行参数, prev_output=目标位置)--> [flight_agent_ros.flight_atomic_action_server；goto 已接入]
[flight_agent_ros.flight_atomic_action_server] --AtomicActionResponse(success, output=到达证据)--> [behavior_executor.sequence_executor]
[behavior_executor.sequence_executor] --AtomicActionRequest(input=拍照参数, prev_output=到达证据)--> [flight_agent_ros.capture_photo_server；已接入]
[flight_agent_ros.capture_photo_server；已接入] --AtomicActionResponse(output=photo_id)--> [behavior_executor.sequence_executor]
[behavior_executor.sequence_executor] --AtomicActionRequest(input=识别参数, prev_output=photo_id)--> [目标异物 inspect_image 服务；未实现]
[目标异物 inspect_image 服务；未实现] --AtomicActionResponse(output=识别结论)--> [behavior_executor.sequence_executor]
[behavior_executor.sequence_executor] --AtomicActionRequest(input=返航参数, prev_output=识别结论)--> [flight_agent_ros.flight_atomic_action_server；rtl 已接入]
[flight_agent_ros.flight_atomic_action_server] --AtomicActionResponse(success, output=返航证据)--> [behavior_executor.sequence_executor]

goto/rtl 共用的飞行原子动作内部链路（每次调用均执行）：
[安全 JSON] --MissionConstraints--> [flight_agent_ros.flight_atomic_action_server.main]
[flight_agent_ros.flight_atomic_action_server.main] --已校验的 MissionConstraints--> [flight_agent_ros.flight_atomic_action_server]
[flight_agent_ros.flight_atomic_action_server] --AtomicActionProposal + MissionConstraints--> [flight_agent.components.safety.execution_gate]
[flight_agent.components.safety.execution_gate] --get_world_state()--> [flight_agent_ros.adapters.flight_execution_backend]
[flight_agent_ros.adapters.flight_execution_backend] --WorldState--> [flight_agent.components.safety.execution_gate]
[Authority reader] --ControlAuthority--> [flight_agent.components.safety.execution_gate]
[flight_agent.components.safety.execution_gate] --AtomicActionProposal + MissionConstraints + WorldState + ControlAuthority--> [flight_agent.components.safety.supervisor]
[flight_agent.components.safety.supervisor] --SafetyDecision--> [flight_agent.components.safety.execution_gate]
[flight_agent.components.safety.execution_gate] --AtomicActionProposal + SafetyDecision--> [TraceRecorder]
[flight_agent.components.safety.execution_gate] --ApprovedSkillCommand(仅批准时)--> [flight_agent_ros.adapters.flight_execution_backend]
[flight_agent_ros.adapters.flight_execution_backend] --SkillResult--> [flight_agent.components.safety.execution_gate]
[flight_agent.components.safety.execution_gate] --ApprovedSkillCommand + SkillResult 或执行异常--> [TraceRecorder]
[flight_agent.components.safety.execution_gate] --SafetyDecision + SkillResult(拒绝时为 None)--> [flight_agent_ros.flight_atomic_action_server]

[behavior_executor.sequence_executor] --ExecuteBehaviorResponse(success, message, info)--> [agent_comm.RosEnv.step]
[agent_comm.RosEnv.step] --get_obs()--> [agent_comm.RosEnv]
[agent_comm.RosEnv] --ObservationRequest--> [observation_manager.observation_manager_node]
[observation_manager.observation_manager_node] --ObservationResponse(新观察)--> [agent_comm.RosEnv]
[agent_comm.RosEnv.step] --(obs, reward, done, info)；执行诊断仅作为飞行扩展保留--> [agent_comm.ros_agent_srv.main]
[操作员] --HumanInput(下一轮；行为完成并再观察后)--> [agent_comm.ros_agent_srv.main]
```

`ExecuteBehaviorRequest` 是整个序列的输入，`AtomicActionRequest` 是逐项调用的输入；`service_name` 选择 ROS 服务，不是 `AtomicActionRequest` 的字段。目标 `flight_agent.contracts.AtomicAction` 是 Safety 批准后的内部执行凭证，不是 `rosllm_srvs/srv/AtomicAction` 的未审查请求。

V1 不建立任务 ID 或任务会话。飞行动作服务以 `--safety-json` 指定新 JSON 文件，启动时加载系统 `MissionConstraints`；缺少或无效文件时不发布服务。Gate 使用这些限制、当前状态和 Authority 等证据。MX-4 1b 已从 Proposal、Gate 和 Supervisor 移除旧任务身份；旧任务模型与 CLI 已按 [MX-4 步骤 6](plans/MX-4-structure-change.md#实施任务)删除。

原子动作仅向下一项传递紧邻前一项的 `output`；巡检照片和识别结论要独立保存，才能在返航后从新观察中取回。若任一步失败，序列停止；运行中安全处置不依赖序列尾部的 `rtl`。

## 5. 安全不变量

1. 普通物理动作没有对应的 `SafetyDecision=APPROVE` 与 `ApprovedSkillCommand` 不得派发；拒绝、读取失败、Trace 写入失败均不得误放行。
2. State 过期、State ID 不匹配、Authority 非 `AGENT_ALLOWED`、PX4 failsafe/健康证据不满足、越围栏/高度/半径、系统限速与飞控限速不一致时拒绝；具体字段、顺序与原因码以 `src/flight_agent/contracts/` 和 `src/flight_agent/components/safety/` 的实现及 `contracts/` 的设计文档核对。
3. V1 仅面向受控 SITL，假定操作员已授权 Agent；飞行动作入口固定提供 `AGENT_ALLOWED`，只满足 Authority 检查，其他 Safety 检查及 PX4 failsafe 保持生效。该假设须在入口说明和运行证据中标明，不代表可信授权机制已实现。`UNKNOWN` 及其他非允许值仍拒绝；可信授予、撤销、失联处理与权限变化复核移入 [V2](plans/V2-optimizations.md#v2-2可信-authority)。7c 已设置默认权限；Takeoff/Land 的真实服务链已按 [7f](plans/M4.md#7f-实施记录)通过 SITL 验证，其余动作按 M4 计划推进。
4. Safety 拒绝、执行失败和超时必须分别保留事实，不得映射为动作成功。
5. 在途危险处置必须有独立于模型推理的路径；当前缺口写入 M7-S。任何测试不得用 PX4 原生 failsafe 覆盖这一缺口。
6. 简单的人工任务/反馈输入是 ROS-LLM 主链的一部分；产品级人工 Plan 审批与复杂操作界面另行设计。V1 自主测评不依赖逐 Skill 审批；接管/急停也不能绕过飞行 Safety。

## 6. 契约与数据流

现存核心模型位于 `src/flight_agent/contracts/models/`：`MissionConstraints`、`NedGeofence`、`WorldState`、`AtomicActionProposal`、`SafetyDecision`、`ApprovedSkillCommand`、`SkillResult` 等。旧任务及占位验证模型已删除。`AtomicActionProposal` 是原 `SkillProposal` 的现名，仍表示 Safety 审查前的内部飞行动作提案。当前 Gate 只接收 `MissionConstraints`，不接收旧任务 ID；`FlightExecutionBackendProtocol` 是核心与 ROS 2 实现的调用边界。模型定义存在不代表相应完整业务已接入。

**目标命名决定**：Agent 对象统一叫 `RosAgent`；对外动作统一叫 `AtomicAction`。当前内部模型 `ApprovedSkillCommand` 目标改名为 `flight_agent.contracts.AtomicAction`，表示 Safety 批准后的单次动作执行凭证；ROS 服务 `rosllm_srvs/srv/AtomicAction` 接收尚未审查的动作请求。两者同名但属于不同模块和信任边界，不得互换或因改名取消审批。MX-4 调用流沿用当前模型名；后续改名须经契约评审并同步代码、导出、测试和测评数据。

目标 V1 每次飞行动作至少关联提案、决策、执行 ID 及完成判定使用的状态证据；Trace 按这些关联信息重建动作因果链，不引入任务 ID。字段变动先审核契约和现有数据集，不以重命名作为功能完成证据。世界坐标、Home、NED/ENU 的实际映射以 `WorldState`、PX4 适配器和对应契约测试为准；不在规格中虚构未接入的状态来源。

## 7. 阶段与验收

| 阶段 | 状态（按当前仓库） | 下一验收条件 |
|---|---|---|
| M0 测评设计与骨架 | 完成基础结构 | Dev/Validation/Frozen Test 持续治理；基础结构不代表冻结结果 |
| M1 ROS 2/PX4/Gazebo 环境 | 已有阶段记录 | 重跑时记录冻结版本和真实 SITL 证据 |
| M2 WorldState/Trace 基础 | 已有阶段记录 | 状态时效已按[报文等级](contracts/safety_contract.md#worldstate-报文新鲜度分级)接入，保留旧格式回放兼容 |
| M3 确定性飞行动作 | 已有阶段记录 | 保留调试入口与 Backend 回归，不把它计作 Agent 任务成功 |
| [MX 主流程骨架贯通](plans/MX.md) | **替身范围完成，2026-11-04 已复核** | 后续进入 M4 默认授权接线与受控 SITL；完整 Episode 等剩余边界见 [MX-5 审计](plans/MX-5-audit.md#发现) |
| M4 Contract/Safety Gate | **已完成（受控场景范围）** | 默认授权、五动作实际服务、重复运行及旧审批清理已收口；限速拒绝等未覆盖项见 [M4 收口结论](plans/M4.md#收口结论)，不代表完整 Agent 任务闭环 |
| [M5 ROS-LLM 有人主链](plans/M5.md) | **完成（已调整的受控 SITL 范围）** | [收口审查](plans/M5.md#4c-收口审查)覆盖真实模型、独立进程、两轮输入、多动作及 Gate 拒绝；3a 解析细则仍暂缓，执行反馈和在途监测按后续阶段推进，不声称完整任务成功或无人续跑 |
| [M6 视觉场景功能 Test](plans/M6.md) | 一周时间盒，进行中；进度见阶段计划 | 接入最小相机/现成检测工具，通过有用场景的真实功能 test 并交接；不在此阶段建设系统 eval |
| [M7 系统 Eval](plans/M7.md) | M6 通过后的一周时间盒，尚未实施 | 复用可用视觉链，输出普通/未见/扰动成功率、同工具基线差值、样本量与区间；新 M7 不恢复旧反馈修正阶段 |
| M7-S 飞行运行中安全专项 | 未开始 | 独立运行中监测、抢占仲裁、处置 Trace 与故障 SITL 测试；不创建任务调度循环 |
| M8–M12 测评、消融与发布 | 未开始；最小 eval/对照已纳入新 M7 计划 | 后续扩展样本、模型/架构比较与发布设施；复用 M7 产物，不重复实施 Runner、Grader 和基线报告 |

旧版项目总工期位于归档规格，因架构与阶段范围变化已失效。当前不承诺项目完成日期；MX 的分项工作量见 [阶段计划](plans/MX.md)，其他阶段待纵向切片后重估。

## 8. 测试、测评与证据

阶段顺序为 [M6 功能 Test](plans/M6.md)通过后进入 [M7 系统 Eval](plans/M7.md)，各一周时间盒。`eval_harness` 的任务运行、判分与系统量化评分按[测评设计](docs/eval_doc/v1_evaluation.md)执行；旧 M7 反馈修正阶段仍取消，新 M7 专指 eval，问题修复以实测证据为依据。

测试分清四类：组件单测、契约测试、从真实装配入口进入的替身集成测试、PX4/Gazebo SITL 端到端测试。替身测试证明软件调用流，不证明飞行；SITL 测试证明指定环境和场景，不证明真实飞机或适航。生产入口尚未接入时，不能以组件测试通过宣称闭环。

`agent_benchmark_sets/dev` 用于开发；`validation` 用于阶段检查；`frozen_test` 只在系统稳定后冻结。`eval_harness` 后续负责 Episode、故障注入、判分和报告，不与测评数据目录混同。任务成功、安全违规、恢复成功、延迟等指标需绑定 Git 提交、环境 manifest、PX4/模型/提示词/Safety 版本与测评集版本；当前没有可对外发布的数值。人工辅助结果与自主结果必须分开报告。

## 9. 开发与版本管理

开发流程、文档归属、审核阈值和版本管理规则以 [AGENT.md](AGENT.md) 为准；本章不另行定义同一规则。

## 10. 待确认事项

1. V1 固定授权假设以第 5 节为准，可信 Authority 仍属 V2；受控 SITL 证据不能作为可信授权实现。
2. 序列上限沿用现有执行器和 [M5 约束](plans/M5.md#约束)。失败后自动 Hold 已按用户决定删除，后续必要性见 [V2-3](plans/V2-optimizations.md#v2-3失败后是否需要额外-hold)；其他在途处置条件仍归 M7-S。飞行动作输入输出见 [服务契约](contracts/rosllm_services.md)。
3. M7-S 的工作量与时间表仍待细分；M5 留存的观察滞后、状态过期拒绝及暂缓解析边界见[收口审查](plans/M5.md#4c-收口审查)。上游 ROS 1 运行时对照仍未完成，源码核对不等于运行验证。

这些是待决策或待实现项，不得在进度表中提前标为完成。

## 11. V2 优化

后续产品迭代事项统一记录在 [V2 优化清单](plans/V2-optimizations.md)，不纳入当前 V1/MX-4 实施范围。
