# 机械臂阅读角

这是一个“无硬件可演示”的可重构空间原型：三条虚拟机械臂共享同一套 AccessHub 策略、审计、事件和急停。

- `/robot`：可视化演示页；
- `GET /api/robot/state`：三条臂的状态；
- `POST /api/robot/scene`：执行场景；
- `southbound serve` 启动后访问 `http://127.0.0.1:8765/robot`。

## 三条臂

| 设备 | 角色 | 能力 |
|---|---|---|
| `arm_lamp` | 阅读灯臂 | `home`、`move_pose`、`look_at`、`point`、`set_light`、`stop`、`reset` |
| `arm_display` | 显示臂 | `home`、`move_pose`、`look_at`、`point`、`set_display`、`stop`、`reset` |
| `arm_gripper` | 夹爪臂 | `home`、`move_pose`、`point`、`press`、`grab`、`release`、`stop`、`reset` |

所有动作都经过 `AccessHub.call`，因此仍受策略、风险等级、确认和审计约束。

## 场景

- **阅读模式**：灯臂调整亮度和色温，显示臂显示“阅读模式”，夹爪回到停靠位；
- **专注模式**：显示臂转向用户，灯光更亮更冷；
- **按一下咖啡机**：夹爪按下物理按钮，展示 Agent 操作非智能设备；
- **急停**：三条臂立即进入 `estopped`，后续动作被拒绝；
- **复位**：清除急停并回到安全停靠位。

## 安全边界

当前是模拟器，但它刻意按照真实机械臂的控制边界设计：

- `press`、`grab`、`release` 是 medium risk，仍应经过策略与审计；
- 急停优先于一切运动；
- 真实机械臂应增加速度、力矩、工作空间和碰撞限制；
- 不允许接近人脸、刀具、热水、燃气和门锁；
- 真实部署必须使用 MoveIt2/ROS2 等运动规划，LLM 只调用高层技能，不生成关节轨迹；
- 高风险动作需要人工确认，急停后需要人工复位。

## 接入真实机械臂

建议先做一条小负载 6-DOF 臂，末端在台灯、平板/小屏幕和软夹爪之间切换：

1. 用 ROS2/MoveIt2 实现 `home`、`move_pose`、`look_at`；
2. 增加力/扭矩阈值、速度缩放和工作空间边界；
3. 用 AprilTag/深度相机定位物理按钮和小物件；
4. 在 `ArmAdapter` 中把 `press`、`grab`、`release` 映射到真实控制器；
5. 保持 `/api/robot/scene` 和 AccessHub 能力面不变，替换模拟器即可。

电视机/大显示器不建议直接挂在低成本机械臂上；先用平板或小显示器验证轨迹、负载和安全边界。
