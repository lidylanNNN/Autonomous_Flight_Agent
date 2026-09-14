# PX4 ROS2 Gazebo Runtime Setup

## Target Stack

This project targets the following M1 runtime stack:

```text
Ubuntu 24.04 LTS
ROS 2 Jazzy
Gazebo Harmonic
PX4 v1.16.2 (54f0455ffcd755534539a7cf33a09a20bf71d29d)
uXRCE-DDS
x500 multicopter SITL
```

## Why This Stack

The local host is Ubuntu 24.04. PX4's ROS 2 guide recommends ROS 2 Jazzy for
Ubuntu 24.04 and Gazebo Harmonic for this simulation path. Gazebo Harmonic also
ships binary packages for Ubuntu Noble.

Ubuntu 22.04 / ROS 2 Humble remains a fallback only if M1 hits a blocker that is
cheaper to solve with a container than on the host.

## Health Check

Run:

```bash
python scripts/runtime/check_runtime_health.py
```

The script checks:

```text
Ubuntu version
Python version
uv
ROS 2 command availability
Gazebo command availability
MicroXRCEAgent availability
PX4 source checkout
ROS 2 Jazzy setup path
```

## Planned Install Order

1. Install ROS 2 Jazzy.
2. Clone PX4-Autopilot v1.16.2 with recursive submodules.
3. Install PX4 development dependencies and Gazebo Harmonic.
4. Install ROS/Gazebo bridge packages.
5. Build and run `make px4_sitl gz_x500`.
6. Start uXRCE-DDS agent.
7. Source ROS 2 / workspace setup files.
8. Verify PX4 topics are visible through `ros2 topic list`.

## Success Criteria

M1 is complete when a cold terminal can:

```text
start PX4 SITL with Gazebo x500
start or verify uXRCE-DDS transport
list ROS 2 topics from PX4
run a repeatable health check
document the exact PX4 commit and environment manifest
```

## Verified Locally

The following checks pass on the current M1 host:

```bash
make px4_sitl
timeout 25s env HEADLESS=1 make px4_sitl gz_x500
```

The bounded smoke test reaches a ready Gazebo world, spawns `x500_0`, starts
PX4 successfully, and initializes the uXRCE-DDS client for UDP port 8888. The
timeout exit is intentional and prevents the simulator from remaining active.

## References

- PX4 ROS 2 User Guide: https://docs.px4.io/main/en/ros2/user_guide
- PX4 uXRCE-DDS Guide: https://docs.px4.io/main/en/middleware/uxrce_dds
- Gazebo Harmonic Ubuntu install: https://gazebosim.org/docs/harmonic/install_ubuntu/
