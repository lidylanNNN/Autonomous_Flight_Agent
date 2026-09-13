'''校验项目运行环境 manifest。'''

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from manifests.schemas.environment_manifest import EnvironmentManifest  # noqa: E402


def main() -> None:
    '''执行 EnvironmentManifest 校验流程。'''

    args = parse_args()
    manifest_data = load_yaml(args.manifest)

    try:
        manifest = EnvironmentManifest.model_validate(manifest_data)
    except ValidationError as exc:
        print(f'FAILED: invalid EnvironmentManifest at {args.manifest}')
        print(exc)
        raise SystemExit(1) from exc

    print(
        f'OK: validated EnvironmentManifest {args.manifest} '
        f'for {manifest.project.name} ({manifest.status})'
    )


def parse_args() -> argparse.Namespace:
    '''解析命令行参数。'''

    parser = argparse.ArgumentParser(description='Validate manifests/environment.yaml.')
    parser.add_argument(
        'manifest',
        nargs='?',
        type=Path,
        default=Path('manifests/environment.yaml'),
        help='environment manifest YAML path',
    )
    return parser.parse_args()


def load_yaml(path: Path) -> dict[str, Any]:
    '''读取 YAML 文件并确保顶层是对象。'''

    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except OSError as exc:
        print(f'FAILED: cannot read {path}: {exc}')
        raise SystemExit(1) from exc
    except yaml.YAMLError as exc:
        print(f'FAILED: invalid YAML at {path}: {exc}')
        raise SystemExit(1) from exc

    if not isinstance(data, dict):
        print(f'FAILED: {path} must contain a YAML mapping at the top level')
        raise SystemExit(1)

    return data


if __name__ == '__main__':
    main()
