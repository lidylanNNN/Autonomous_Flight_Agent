'''校验 mission set split manifest 和 family-level 隔离规则。'''

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_benchmark_sets.schemas.mission_case import MissionEvalCase
from agent_benchmark_sets.schemas.split_manifest import MissionSplitManifest

DEFAULT_SPLITS = ('dev', 'validation', 'frozen_test')


@dataclass(frozen=True)
class SplitIssue:
    '''描述一条 split 校验问题。'''

    path: Path
    message: str


def main() -> None:
    '''执行 split manifest 与 family-level 规则校验。'''

    args = parse_args()
    manifests: dict[str, MissionSplitManifest] = {}
    issues: list[SplitIssue] = []

    for split in args.splits:
        manifest_path = args.root / split / 'manifest.json'
        manifest, manifest_issues = load_split_manifest(manifest_path)
        issues.extend(manifest_issues)
        if manifest is None:
            continue

        manifests[split] = manifest
        issues.extend(validate_manifest_files(args.root / split, manifest))

    issues.extend(validate_family_isolation(args.root, manifests))

    if issues:
        print_report(issues)
        raise SystemExit(1)

    print(f'OK: validated {len(manifests)} split manifest(s) under {args.root}')


def parse_args() -> argparse.Namespace:
    '''解析命令行参数。'''

    parser = argparse.ArgumentParser(
        description='Validate mission set split manifests and family-level isolation.'
    )
    parser.add_argument(
        '--root',
        type=Path,
        default=Path('agent_benchmark_sets'),
        help='Agent benchmark sets root directory',
    )
    parser.add_argument(
        '--splits',
        nargs='+',
        default=list(DEFAULT_SPLITS),
        help='split directories to validate',
    )
    return parser.parse_args()


def load_split_manifest(path: Path) -> tuple[MissionSplitManifest | None, list[SplitIssue]]:
    '''读取并校验 split manifest。'''

    if not path.exists():
        return None, [SplitIssue(path, 'split manifest is missing')]

    try:
        manifest = MissionSplitManifest.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return None, [SplitIssue(path, f'invalid split manifest: {exc}')]

    return manifest, []


def validate_manifest_files(split_dir: Path, manifest: MissionSplitManifest) -> list[SplitIssue]:
    '''校验 manifest 内声明的 case 文件与 family 列表。'''

    issues: list[SplitIssue] = []
    derived_families: set[str] = set()

    for case_file in manifest.case_files:
        case_path = split_dir / case_file
        if not case_path.exists():
            issues.append(SplitIssue(case_path, 'case file declared in manifest is missing'))
            continue

        try:
            case = MissionEvalCase.model_validate_json(case_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            issues.append(SplitIssue(case_path, f'invalid MissionEvalCase: {exc}'))
            continue

        expected_name = f'{case.case_id}.json'
        if case_path.name != expected_name:
            issues.append(SplitIssue(case_path, f'file name should be {expected_name}'))

        derived_families.add(case.case_id.rsplit('-', maxsplit=1)[0])

    declared_families = set(manifest.family_ids)
    if derived_families != declared_families:
        issues.append(
            SplitIssue(
                split_dir / 'manifest.json',
                f'family_ids {sorted(declared_families)} do not match case files '
                f'{sorted(derived_families)}',
            )
        )

    return issues


def validate_family_isolation(
    root: Path,
    manifests: dict[str, MissionSplitManifest],
) -> list[SplitIssue]:
    '''校验同一 family 不会跨 split 出现。'''

    issues: list[SplitIssue] = []
    family_to_splits: dict[str, list[str]] = {}

    for split, manifest in manifests.items():
        for family_id in manifest.family_ids:
            family_to_splits.setdefault(family_id, []).append(split)

    for family_id, splits in sorted(family_to_splits.items()):
        if len(splits) > 1:
            issues.append(
                SplitIssue(
                    root / family_id,
                    f'family appears in multiple splits: {", ".join(sorted(splits))}',
                )
            )

    return issues


def print_report(issues: list[SplitIssue]) -> None:
    '''输出 split 校验错误报告。'''

    print(f'FAILED: found {len(issues)} split issue(s)')
    for issue in issues:
        print(f'- {issue.path}: {issue.message}')


if __name__ == '__main__':
    main()
