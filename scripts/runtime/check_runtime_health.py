'''检查 M1 PX4 / ROS2 / Gazebo runtime 的本机可用性。'''

from __future__ import annotations

import platform
import os
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    '''执行只读 runtime health check。'''

    checks = [
        check_command('uv', ['uv', '--version']),
        check_command_or_install_path(
            'ros2',
            ['ros2', '--help'],
            Path('/opt/ros/jazzy/bin/ros2'),
            'source /opt/ros/jazzy/setup.bash',
        ),
        check_command('gz', ['gz', 'sim', '--versions']),
        check_command_or_install_path(
            'MicroXRCEAgent',
            ['MicroXRCEAgent', '--help'],
            Path.home() / '.local/bin/MicroXRCEAgent',
            'export PATH="/home/lnz/.local/bin:$PATH"',
        ),
        check_path('ROS 2 Jazzy setup', Path('/opt/ros/jazzy/setup.bash')),
        check_path('PX4 source', Path.home() / 'PX4-Autopilot'),
        check_path(
            'PX4 SITL binary',
            Path.home()
            / 'PX4-Autopilot/build/px4_sitl_default/bin/px4',
        ),
    ]

    print(f'Host: {platform.platform()}')
    print(f'Python: {platform.python_version()}')
    print()

    failed = False
    for check in checks:
        print(check)
        failed = failed or check.startswith('FAIL')

    if failed:
        raise SystemExit(1)


def check_command(name: str, command: list[str]) -> str:
    '''检查命令是否存在并返回版本信息。'''

    executable = shutil.which(command[0])
    if executable is None:
        return f'FAIL {name}: command not found'

    try:
        env = None
        if command[0] == 'MicroXRCEAgent':
            env = os.environ.copy()
            env['LD_LIBRARY_PATH'] = '/home/lnz/.local/lib:' + env.get(
                'LD_LIBRARY_PATH', ''
            )
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'FAIL {name}: {exc}'

    output = (result.stdout or result.stderr).strip().splitlines()
    summary = output[0] if output else f'exit code {result.returncode}'
    if result.returncode != 0:
        return f'WARN {name}: {summary}'

    return f'OK {name}: {summary}'


def check_command_or_install_path(
    name: str,
    command: list[str],
    install_path: Path,
    activation_hint: str,
) -> str:
    '''区分未安装命令与尚未激活的已安装命令。'''

    if shutil.which(command[0]) is not None:
        return check_command(name, command)
    if install_path.exists():
        return f'WARN {name}: installed; activate with `{activation_hint}`'
    return f'FAIL {name}: command not found and missing {install_path}'


def check_path(name: str, path: Path) -> str:
    '''检查本地路径是否存在。'''

    if path.exists():
        return f'OK {name}: {path}'
    return f'FAIL {name}: missing {path}'


if __name__ == '__main__':
    main()
