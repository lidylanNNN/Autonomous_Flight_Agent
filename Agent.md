# Agent.md

本文档说明后续开发 Agent 应如何读取 `DEV_SPEC.md`。`DEV_SPEC.md` 是 Autonomous Flight Agent V1 的 Single Source of Truth；实现、测试、评测、文档、仓库结构和里程碑推进应优先以它为准。

## 可读取的信息

Agent 可以从 `DEV_SPEC.md` 读取以下项：

- 项目定义：Autonomous Flight Agent 是基于 ROS2 + PX4 + Gazebo 的自主飞行机器人任务执行系统。
- 当前状态、当前阶段和下一步：读取 `DEV_SPEC.md` 的 `Progress Management`、`Current Progress Board` 和对应 Milestone，不在本文档重复定义。
- V1 目标：可复现 SITL 环境、Flight Skills、Mission Contract、Safety Supervisor、State Verifier、Recovery / Replanning、Trace、Evaluation、Ablation、Docker / CI / Final Report。
- V1 非目标：LLM 不直接输出 actuator、attitude、body-rate、thrust；不维持实时控制环；不自研 PX4；不做 eVTOL / VTOL、Multi-UAV、真实飞机测试、复杂 UI 等扩展。
- 架构边界：LLM 只处于 Mission / Skill Level；PX4 保留底层飞控和原生 failsafe；Safety Supervisor 是执行前确定性仲裁层；State Verifier 根据实际状态判定执行结果。
- Runtime 架构：Observe -> Plan -> Propose -> Safety Check -> Act -> Verify -> Replan / Next。
- 控制时序：LLM loop、Skill execution loop、PX4 control loop 分离。
- 核心数据契约：`MissionRequest`、`MissionContract`、`WorldState`、`MissionPlan`、`SkillProposal`、`SafetyDecision`、`SkillResult`、`VerificationResult`、`TraceEvent`、`MissionEvalCase`。
- ROS2 / PX4 集成约束：PX4 与 ROS2 使用 uXRCE-DDS；Agent / Safety / Evaluation 不直接 import PX4 topic，必须通过 Adapter boundary。
- Flight Skills：Observe、Takeoff、GoTo、Hold、RTL、Land 及 Skill Registry。
- LLM Planner 责任：自然语言任务理解、高层计划生成、Skill 选择、失败后的任务级 replanning；不负责安全放行、实时控制、PX4 protocol correctness。
- Safety Supervisor 规则：按 Schema、Current State、Geofence、Flight Envelope、Command Sequence、Authority、Human Approval 等顺序做确定性校验。
- State Verifier 规则：Takeoff、GoTo、Hold、RTL、Land 的成功判定必须来自状态观测，而不是相信 LLM 或 PX4 ACK。
- Recovery / Replanning 设计：紧急安全行为由 deterministic recovery policy 兜底；LLM replanning 只在安全边界内做任务层调整。
- Trace 要求：完整记录 Mission、Plan、Proposal、Safety Decision、ROS2 Dispatch、PX4 ACK、Skill Result、Verification、Recovery 等事件。
- Mission Set 策略：测评集从 M0 开始持续建设，不等到 M8；`DEV_SPEC.md` 使用 `mission_sets/` 保存 Dev / Validation / Frozen Test 数据。
- Evaluation 策略：评估方法和评测执行逻辑与测评集分开，包含 Unit / Contract / Safety、Agent Simulation、PX4/Gazebo E2E 三层。
- Frozen Mission Set 原则：最终 Task Success、Hard Safety Violation、Recovery Success、Latency 等指标主要来自 PX4/Gazebo Frozen Mission Set。
- Metrics 定义：Task Success、Hard Safety Violation Rate、Recovery Success、Valid Tool Call Rate、Latency、Safety Intervention Rate、PX4 Reject Rate、LLM Calls / Mission 等。
- Fault Injection 分层：Pure Software、ROS2 Adapter Fault Proxy、PX4/Gazebo Fault Scenario。
- Ablation 设计：B0 Scripted、B1 Agent Baseline、B2 +Safety、B3 +Verifier、B4 +Recovery / Replanning。
- Milestone 顺序和 DoD：M0-M12 的交付物、依赖、完成条件、文档路径和 release gates。
- Repository Skeleton：目标仓库结构、模块交付地图、mock delivery contract、最小 M0 skeleton 均已在 `DEV_SPEC.md` 第 21 章定义。`Agent.md` 不重复定义目录树，执行时应回到 `DEV_SPEC.md` 读取最新结构。
- 项目治理规则：SSOT、Git 策略、文件存储策略、ADR、版本规则、contract-first change、safety change management、dataset governance、dependency management、secrets、code quality、test governance、release / tagging。
- Safety Invariants：未经 `SafetyDecision=APPROVE` 的 Skill 不得执行；stale `WorldState` 不得批准飞行动作；越界 waypoint、超高度目标、PX4 failsafe active 等必须阻断。
- 发布门槛：Infrastructure、Safety Logic、Agent Validation、Frozen Test 四类 release gates。
- 简历/展示边界：Task Success / Safety / Recovery 数字目前是占位，必须由真实 evaluation report 替换后才能对外声称。

## 不应误读的信息

- `DEV_SPEC.md` 描述的是 V1 目标态和开发计划，不代表当前代码已经实现。
- `DEV_SPEC.md` 中的 Repository Skeleton 是目标结构和落地顺序，不代表当前仓库已经具备所有目录和文件。
- 任何性能指标、成功率、安全率、恢复率，在没有真实 Evaluation Report 前都不能写成已完成成果。
- Public benchmark 只能作为 UAV reasoning / scenario mapping 参考，不能替代 PX4/Gazebo closed-loop benchmark。
- LLM 不能绕过 Mission Contract、Safety Supervisor、State Verifier、PX4 failsafe 或 Human Approval。
- 为了赶进度，不允许删除 Safety、Verifier、Evaluation 或 Trace 这些核心边界。

## 仓库结构

仓库结构不在本文档中重新定义。Agent 需要仓库布局时，应读取 `DEV_SPEC.md`：

- 第 21 章 `Repository Skeleton`：核心代码、ROS2/PX4 adapter、mission sets、evaluation harness、tests 的隔离区域、依赖方向、硬规则。
- 第 21.1 节 `Final Repository Skeleton`：最终目标目录树。
- 第 21.2 节 `Module Delivery Map`：模块、代码路径、文档路径、最低测试和首次里程碑。
- 第 21.6 节 `M0 Minimal Skeleton`：当前 M0 应优先落地的最小结构。

若实际仓库结构与 `DEV_SPEC.md` 不一致，默认以 `DEV_SPEC.md` 为设计目标；只有在完成对应 Milestone 和 DoD 后，才能把目标结构视为已落地。

## 版本管理

版本管理方式已在 `DEV_SPEC.md` 第 21.8 章定义。Agent 执行时应遵守：

- 项目范围、架构、开发顺序以 `DEV_SPEC.md` 为准。
- 当前开发进度以 Progress Board 和 `docs/milestones/Mx.md` 为准，不以聊天记录为准。
- 架构决策写入 `docs/adr/ADR-xxx.md`。
- `main` 应保持可构建、可运行；功能开发使用短生命周期 `feature/*`，Bug 使用 `fix/*`，高风险实验使用 `experiment/*`。
- 禁止对 `main` force push。
- Milestone 完成后必须绑定 Git Commit，并按 `DEV_SPEC.md` 要求打 Tag。
- Frozen Test、Release、Ablation 结果必须绑定 Git Commit、Tag、环境 Manifest、测评集版本、模型/Prompt/Safety/PX4 等版本信息。
- 以下变化必须升级 `DEV_SPEC.md` 版本：Architecture、Contract、Milestone、Safety Boundary、Evaluation Protocol、Repository Structure、Release Gate。
- `DEV_SPEC.md` 始终表示当前版本，历史版本由 Git/Tag 保存。

## Agent 执行规则

1. 开始任何开发前，先读取 `DEV_SPEC.md` 对应章节。
2. 如果实现会改变 contract、safety、runtime boundary、evaluation schema 或 milestone scope，先更新 `DEV_SPEC.md` 或新增 ADR。
3. 当前 Milestone、开发重点和下一步必须从 `DEV_SPEC.md` 读取，不在本文档硬编码。
4. 每个 Milestone 只有满足交付物完成、DoD 满足、必要测试通过、trace/eval artifact 生成时，才能标记为 `DONE`。
