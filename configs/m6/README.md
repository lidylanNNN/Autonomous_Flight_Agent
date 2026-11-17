# M6 单人物场景 v1

本目录是 T01b 场景配置与资源来源的权威位置。冻结 PX4/Gazebo 版本见环境 manifest；使用已有 `default` 世界、`x500_mono_cam` 实例 0 和单路前视相机，不替换飞行控制或物理插件。操作命令见[运行手册](../../docs/runtime_setup/px4_ros2_gazebo.md#m6-单人物场景)。

## 配置

- `target`：装载 `person.sdf` 中唯一静态实体 `m6_person`，类别为 `person`。实体姿态以该 SDF 为准，不在其他配置重复定义。
- `empty`：移除该实体，其余世界、相机和飞行配置不变；不通过把人物移到镜头外伪造无目标场景。
- 两种配置只在地面操作。完整复现需停止上一轮仿真再重新启动；配置可重复执行，每次先移除已有同名实体，再按配置重建。

## 预定观察点

以下是后续任务使用的固定位置，不代表已取得飞行或画面证据。世界坐标为 ENU，PX4 `goto` 输入为局部 NED 的 north/east 与正向高度；本场景以默认出生点为局部水平原点。

| 点位 | 世界 ENU (x, y, z)，米 | goto (north_m, east_m, altitude_m) |
|---|---|---|
| P1 | (3, 0, 1.5) | (0, 3, 1.5) |
| P2 | (3, -1, 1.5) | (-1, 3, 1.5) |

不新增航向控制；后续出图试跑必须核对实际相机朝向，必要时在 T01c 依据图像调整场景版本，不能按预定点位推断可见。人物真值不输入 Agent 或检测器；任务可使用已知观察点，检测结论仍须来自实际图片。

## 来源与许可

人物来自 [osrf/gazebo_models/person_standing](https://github.com/osrf/gazebo_models/tree/8163eb4b5e7e21985c6591d1c0bfb56468c0093f/person_standing)，固定提交 `8163eb4b5e7e21985c6591d1c0bfb56468c0093f`。模型元数据版本为 1.0，作者 Marina Kollmitz，使用 MakeHuman 创建；原始 `model.config` 保留作者信息。仓库上游许可为 Creative Commons Attribution 3.0 Unported，原文保存在 `models/person_standing/LICENSE`，不套用本项目代码许可。

仅复制原始 `model.sdf`、`model.config`、`meshes/standing.dae` 和网格实际引用的九张纹理；这些文件未修改。静态属性及固定姿态由外部 `person.sdf` 设置。`resources.sha256` 固定本地原始资源内容，运行配置前校验；运行时只使用本地模型，不访问 Fuel 或下载其他场景。

模型在 Gazebo 中装载成功只证明场景资源可用；视觉可辨识性及现成检测工具能否检出，由 T01c 实测决定。
