# Autonomous Flight Agent 开发规格 v2.0

> 状态：待评审。基线：2026-10-16 的 `547f2ce`；本次仅重写文档，不代表代码迁移已完成。
> 历史规格：[DEV_SPEC v1.44](docs/archive/DEV_SPEC_v1.44.md)。历史文件保留决策过程，不再作为当前实现或新开发的验收依据。
> 适用范围：V1 飞行机器人任务系统、仓库结构、开发顺序、测评和版本管理。代码事实以实际生产入口及测试证据为准；本文目标不自动等于已实现。

## 1. 项目定义

本项目探索把自然语言任务变成可审查的飞行动作，并在 PX4/Gazebo 中验证执行结果。目标是将 ROS-LLM 的任务、动作序列、执行、观察反馈闭环适配到 **ROS 2 Jazzy + PX4**，而不是移植其 ROS Noetic 环境或照搬机器人动作接口。

目标使用场景是临时点位任务：操作员描述要去的位置和观察目的，系统选择受限动作、执行并返回可核对的飞行状态。相对手动逐条操控或预先制作固定航线，潜在收益是减少任务准备和重复操作；**节省的人力、时间与成本尚无实测，不能在对外材料中写成已取得的效果**。

系统边界：LLM 只负责任务理解与高层动作选择；确定性 Safety 决定物理动作是否允许；执行后由独立观测验证结果；ROS 2 负责通信与适配；PX4 保持姿态、导航控制和原生 failsafe。Agent 不输出电机、推力、角速率或姿态闭环指令，不替代飞控固件。V1 不包含真实飞机测试、多机协同、视觉搜索或适航声明。

设计依据分三层，不混称为同一技术方案：

1. **系统调用流**：以 [ROS-LLM 开源项目](https://github.com/huawei-noah/HEBO/tree/master/ROSLLM)为架构基线，先做有人输入的一轮动作序列与观察反馈，再考虑无人续跑。具体适配差异必须记录。
2. **产品交互决策点**：参考 [DJI FlightHub 2 Copilot 公开手册](https://fh.dji.com/user-manual/cn/copilot-assistant/copilot-remote-control.html)讨论计划确认、接管和紧急停止；这不代表知道 DJI 的内部实现，也不把尚未实现的交互写成当前能力。
3. **飞行行为与安全条件**：以冻结的 PX4/ROS 2 接口、本仓库代码及 SITL 实测为准。产品参照不得覆盖确定性安全边界或 PX4 原生 failsafe。

## 2. 当前实现基线

下表描述仓库当前状态，不是目标架构图。`docs/milestones/M0.md` 至 `M4.md` 保存阶段记录；未建立的 M5 以后里程碑文件不能当作现存交付物。

| 能力 | 当前代码/入口 | 可声称的边界 |
|---|---|---|
| 环境与状态 | `ros2_ws/`、`scripts/ros2_px4/`、`Px4WorldStateAdapter`、`WorldStateAggregator` | M1/M2 的 PX4/Gazebo、ROS 2 消息和状态聚合基础已建立；运行环境以 `manifests/environment.yaml` 为准 |
| 确定性飞行动作 | `flight_execution_backend.py`、PX4 Command/Offboard adapters、Skill Registry | Takeoff、GoTo、Hold、RTL、Land 有代码映射；M3 调试脚本有 SITL 验证记录，不等于 Agent 任务闭环 |
| 任务主链 | `mission_cli.py -> MissionService.run()` | 只识别固定语句 `起飞到10米`；其他语句返回 `UNSUPPORTED`，无 LLM 或通用规划器 |
| 执行前 Safety | `SafetyExecutionGate -> SafetySupervisor -> FlightExecutionBackend` | Gate 和检查器、决策 Trace 已实现，并有替身集成测试；正式任务入口的 Authority 当前传入 `UNKNOWN`，因此不能声称已在真实任务入口获批飞行 |
| 验证与恢复 | `MissionService._verify_placeholder()`、`_recover_placeholder()` | 验证只返回 `INCONCLUSIVE`，恢复只返回 `None`；没有任务成功判定、重新规划或安全收尾实现 |
| Trace | `TraceRecorder`、`TraceReplay` | 可记录/回放 WorldState 与 SafetyDecision；完整 Episode 因果链尚未完成 |
| 测评数据 | `agent_benchmark_sets/`、schema 与 validation scripts | Dev 用例存在；Validation/Frozen Test 目前只是预留 manifest，不得报告冻结测评指标 |
| 旧审批原型 | `human_approval` 模型、组件、Mock 和测试 | 尚存仓库，但不是当前自主执行链，不等于任务级人工确认产品功能 |

**两条实际入口必须分开报告：**

```text
受控任务入口：mission_cli --instruction/--contract-json
  -> MissionService 固定语句占位 -> Proposal -> Gate -> Supervisor
  -> 仅 APPROVE 才可 Backend.execute -> SkillResult -> INCONCLUSIVE
  当前 Authority=UNKNOWN；不能把替身测试的批准说成 SITL 任务批准。

M3 调试入口：mission_cli --legacy-m3-direct
  -> 固定飞行脚本 -> PX4 ROS 2 Backend -> PX4
  显式绕过任务 Gate，只作 M3 调试与回归，不进入自主安全测评。
```

## 3. 固定环境与仓库边界

`manifests/environment.yaml` 固定 Ubuntu 24.04、Python 3.12/uv、ROS 2 Jazzy、PX4 v1.16.2 对应提交、与其匹配的内嵌 `px4_msgs`、Gazebo Harmonic、x500 和 uXRCE-DDS。`pyproject.toml` 与 `uv.lock` 管理核心 Python 环境；ROS 包通过 `ros2_ws`/colcon 构建。Docker/Compose 仍是计划，不是当前部署证据。环境 manifest 中较早的 `spec_version` 是历史字段，更新前不得把它当成本规格版本。

当前仓库的职责分界：

```text
src/flight_agent/contracts/          Pydantic 数据契约、Protocol、错误类型
src/flight_agent/components/         Safety、Skill Registry、Mock Backend、State、Trace
src/flight_agent/workflows/mission/  当前固定语句 MissionService；待迁移旧链
ros2_ws/src/flight_agent_ros/        ROS 2 入口、PX4 状态/命令/参数/Offboard 适配
ros2_ws/src/px4_msgs/                与冻结 PX4 版本匹配的消息定义
agent_benchmark_sets/               Agent 测评数据与 schema，不是在线任务或 PX4 Mission
eval_harness/                        测评辅助代码；完整 Episode Runner 尚未实现
tests/                               单元、契约和替身集成测试
docs/、manifests/、configs/           阶段记录、环境声明和配置；预留配置不自动生效
```

核心代码不得直接依赖 `rclpy`、`px4_msgs` 或 ROS Topic 名；PX4 适配留在 ROS 包。`flight_agent` 不依赖 `eval_harness`，测评集不反向依赖产品代码。目录存在不等于能力已接入；新增目录应由实际生产调用链驱动，不先批量铺设空壳。

## 4. 目标调用链与职责

M5 第一阶段只做**有人闭环**：人提交一轮语句，Agent 给出有界原子动作序列，执行器逐项调用物理 Tool；序列完成或首项失败后生成 Observation 并返回给人。失败时先停止序列，按可信状态运行确定性的 Hold/RTL 处置；执行器不自行做任务级重新规划。M7 才允许 Agent 消费 Observation 后无人发起下一轮。以上是目标，不是现有实现。

```text
人工任务输入
  -> Agent Loop（LLM 推理、受限动作序列选择）
  -> Runtime Tool Invocation（PreToolUse/PostToolUse/OnToolError/Stop Hook）
  -> 物理 Tool -> SafetyExecutionGate -> SafetySupervisor
  -> ApprovedSkillCommand -> FlightExecutionBackend -> ROS 2/PX4
  -> SkillResult -> State Verifier -> Observation -> 人工下一轮输入

并行安全路径：Runtime SafetyMonitor（待实现）可抢占在途动作；
PX4 native failsafe 独立运行，不等待 Agent 或 Monitor。
```

- **Agent Loop** 是唯一任务决策者，负责语义解释、动作选择、预算与后续策略；不得输出低层控制量。
- **Runtime Hooks** 只处理工具边界的校验、权限、超时/取消、Trace 和结果归一，不建立第二套任务决策循环。
- **物理 Safety Gate** 对每个 Proposal 重新读取可信状态与 Authority；只有直接 `APPROVE` 才派发。通用 Hook 不能替代它。
- **Backend** 把已批准动作映射到 PX4 命令/Offboard，ACK 只是传输证据，不是 Skill 或任务完成证据。
- **Verifier** 基于后续状态判定动作完成；不能相信模型文字、ACK 或 Backend 返回的单一成功位。
- **Runtime SafetyMonitor** 独立监测飞行中的危险与链路失效，并处理抢占/控制权仲裁；当前未实现，不能把 PX4 failsafe 冒充本模块。

旧 `MissionService` 的固定语句识别、`PlanStep` 调度、验证和恢复占位不作为新 Agent 的容器。**迁移顺序**：先把 M4 Gate 的可运行装配与验收移出旧入口，再删除旧服务及仅服务旧链的契约/测试，最后建立 M5 新入口。删除之前必须保持现有测试和 M3 调试路径可用；本规格不授权直接执行删除。

## 5. 安全不变量

1. 普通物理动作没有对应的 `SafetyDecision=APPROVE` 与 `ApprovedSkillCommand` 不得派发；拒绝、读取失败、Trace 写入失败均不得误放行。
2. State 过期、State ID 不匹配、Authority 不可信、PX4 failsafe/健康证据不满足、越围栏/高度/半径、任务限速与飞控限速不一致时拒绝；具体字段、顺序与原因码以 `contracts/` 和 `components/safety/` 为准。
3. 对 `UNKNOWN` Authority 必须维持拒绝。接入真实控制权来源前，不允许用测试注入的 `AGENT_ALLOWED` 宣称生产 Gate 可飞。
4. Safety 拒绝、执行失败和不确定验证必须分别保留事实，不得映射为任务成功。`INCONCLUSIVE` 不是成功。
5. 在途危险处置必须有独立于模型推理的路径；当前缺口写入 M7-S。任何测试不得用 PX4 原生 failsafe 覆盖这一缺口。
6. 人工 Plan 确认与操作界面属于自主测评后的产品阶段；V1 自主测评不依赖逐 Skill 审批。接管/急停需求可先建模，但不能借此绕过 Safety。

## 6. 契约与数据流

现存核心模型位于 `src/flight_agent/contracts/models/`：`MissionRequest`、`MissionContract`、`WorldState`、`SkillProposal`、`SafetyDecision`、`ApprovedSkillCommand`、`SkillResult`，以及旧主链的 `MissionPlan`、`PlanStep`、`MissionRunResult` 和占位 `VerificationResult`。`FlightExecutionBackendProtocol` 是核心与 ROS 2 实现的调用边界。模型定义存在不代表相应完整业务已接入。

目标 V1 每次动作至少关联任务、提案、决策、执行、状态验证 ID；Trace 要能按这些 ID 重建因果链。字段变动先审核契约和现有数据集，不以重命名作为功能完成证据。世界坐标、Home、NED/ENU 的实际映射以 `WorldState`、PX4 适配器和对应契约测试为准；不在规格中虚构未接入的状态来源。

## 7. 阶段与验收

| 阶段 | 状态（按当前仓库） | 下一验收条件 |
|---|---|---|
| M0 测评设计与骨架 | 完成基础结构 | Dev/Validation/Frozen Test 持续治理；基础结构不代表冻结结果 |
| M1 ROS 2/PX4/Gazebo 环境 | 已有阶段记录 | 重跑时记录冻结版本和真实 SITL 证据 |
| M2 WorldState/Trace 基础 | 已有阶段记录 | 状态时效与回放回归保持可用 |
| M3 确定性飞行动作 | 已有阶段记录 | 保留调试入口与 Backend 回归，不把它计作 Agent 任务成功 |
| M4 Contract/Safety Gate | **进行中** | 可信 Authority 来源；独立于旧 MissionService 的 Gate 装配；拒绝与批准 Trace；真实 PX4/Gazebo 受控链验证及边界回归 |
| M5 有人 Agent 闭环 | 未开始 | 人工语句 -> 模型选择的有界序列 -> 逐项 Safety/执行 -> 失败安全处置 -> 整段 Observation；生产入口和替身/SITL 测试均可追踪 |
| M6 独立 State Verifier | 未开始 | 五个飞行动作的状态证据、超时与假成功测试；不再返回占位 `INCONCLUSIVE` 作为已完成结果 |
| M7 无人续跑/任务恢复 | 未开始 | Agent 根据 Observation 决定下一轮或停止；预算、终止和失败分类可审计 |
| M7-S Runtime SafetyMonitor | 未开始 | 独立运行中监测、抢占仲裁、处置 Trace 与故障 SITL 测试 |
| M8–M12 测评、消融与发布 | 未开始 | 完整 Episode Runner、冻结集、基线/消融、报告、Docker/CI；每阶段单独定义 DoD |

**当前下一步只做 M4 收口设计与验证**。先明确可信 Authority 的来源和独立 Gate 入口如何创建、持有、调用，再拆成小批次实施。M4 验收不能依赖尚不存在的 LLM。M5 前对 ROS-LLM 原始调用流与本项目飞行适配差异写明输入、输出、失败处理和测试，不凭目录名宣称已落地。

旧版工期数字位于归档规格，因 M5 有人序列、M7 无人续跑及 M7-S 范围变化已失效。当前不承诺完成日期；M4 收口后基于纵向切片重新估时。

## 8. 测试、测评与证据

测试分清四类：组件单测、契约测试、从真实装配入口进入的替身集成测试、PX4/Gazebo SITL 端到端测试。替身测试证明软件调用流，不证明飞行；SITL 测试证明指定环境和场景，不证明真实飞机或适航。生产入口尚未接入时，不能以组件测试通过宣称闭环。

`agent_benchmark_sets/dev` 用于开发；`validation` 用于阶段检查；`frozen_test` 只在系统稳定后冻结。`eval_harness` 后续负责 Episode、故障注入、判分和报告，不与测评数据目录混同。任务成功、安全违规、恢复成功、延迟等指标需绑定 Git 提交、环境 manifest、PX4/模型/提示词/Safety 版本与测评集版本；当前没有可对外发布的数值。人工辅助结果与自主结果必须分开报告。

## 9. 开发与版本管理

- 新功能先列生产入口、触发条件、输入输出、创建者/持有者、调用流和验证方式；优先最小纵向切片，再完善组件。不得把单测中的对象装配当作生产接入。
- 修改业务代码、契约、测试或配置前，按 `Agent.md` 汇报本批变更、对象/函数输入输出、数据流节点和验证；涉及结构、Safety 或跨模块边界时先等用户确认。用户仅要求讨论或文档评审时不得擅改代码。
- 每批完成后列实际生产调用链和未接入环节，给出对应文件位置与测试结果。未经用户明确要求，不提交或推送。
- `main` 保持可回归；采用短生命周期变更分支，禁止 force push `main`。提交使用中文说明并携带项目版本号；里程碑、冻结测评与发布须绑定 commit/tag/环境清单。具体版本号由当次变更确定，不把规格版本当软件发布版本。
- 影响架构、契约、安全边界、里程碑、测评协议、仓库结构或发布门槛时，先修订本规格或新增 `docs/adr/` 决策。历史规格保留在 `docs/archive/`；旧章节编号不是当前规范。

## 10. 待确认事项

1. 可信 Authority 的生产来源与失联时行为；这直接决定 M4 是否能受控批准并完成 SITL 验收。
2. M5 有人任务入口的输入形式、动作序列上限、失败后 Hold/RTL 选择条件，以及人工下一轮输入的触发方式。
3. 旧 `MissionService`、`MissionPlan`/`PlanStep`、审批原型的迁移/归档边界与跨目录引用清单。先建立独立 M4 Gate 验收路径，再实施代码删除。
4. M5/M7/M7-S 的实际工作量与时间表，待 M4 收口和第一条真实 Agent 纵向切片后重估。

这些是待决策或待实现项，不得在进度表中提前标为完成。
