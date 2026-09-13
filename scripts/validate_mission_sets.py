'''校验 mission_sets 中的 manifest 与 MissionEvalCase JSON。'''

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mission_sets.schemas.mission_case import MissionEvalCase, MissionSetManifest  # noqa: E402


DEFAULT_SPLITS = ('dev', 'validation', 'frozen_test')
REQUIRED_DEV_VARIANTS = ('C', 'F', 'N')
VARIANT_TAGS = {
    'C': 'constraint_boundary',
    'F': 'fault_disturbance',
    'N': 'normal',
}


@dataclass(frozen=True)
class ValidationIssue:
    '''描述一条测评集校验问题。'''

    path: Path
    message: str


def main() -> None:
    '''执行命令行校验流程。'''

    args = parse_args()
    root = args.root
    issues: list[ValidationIssue] = []
    checked_cases = 0
    checked_manifests = 0

    for split in args.splits:
        split_dir = root / split
        manifest_path = split_dir / 'manifest.json'

        if not split_dir.exists():
            issues.append(ValidationIssue(split_dir, 'split directory does not exist'))
            continue

        if not manifest_path.exists():
            print(f'SKIP {split}: reserved split has no manifest.json yet')
            continue

        manifest, manifest_issues = load_manifest(manifest_path)
        issues.extend(manifest_issues)
        if manifest is None:
            continue

        checked_manifests += 1
        split_cases, case_issues = load_cases(split_dir, manifest)
        checked_cases += len(split_cases)
        issues.extend(case_issues)
        issues.extend(validate_manifest_consistency(manifest_path, split, manifest, split_cases))
        issues.extend(validate_case_set_consistency(split_dir, split, split_cases))

    if issues:
        print_report(issues)
        raise SystemExit(1)

    print(
        f'OK: validated {checked_cases} mission cases '
        f'across {checked_manifests} manifest(s) under {root}'
    )


def parse_args() -> argparse.Namespace:
    '''解析命令行参数。'''

    parser = argparse.ArgumentParser(
        description='Validate mission set manifests and MissionEvalCase JSON files.'
    )
    parser.add_argument(
        '--root',
        type=Path,
        default=Path('mission_sets'),
        help='mission_sets root directory',
    )
    parser.add_argument(
        '--splits',
        nargs='+',
        default=list(DEFAULT_SPLITS),
        help='split directories to validate',
    )
    return parser.parse_args()


def load_manifest(path: Path) -> tuple[MissionSetManifest | None, list[ValidationIssue]]:
    '''读取并校验单个 manifest。'''

    try:
        manifest = MissionSetManifest.model_validate_json(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        return None, [ValidationIssue(path, f'invalid manifest: {exc}')]

    return manifest, []


def load_cases(
    split_dir: Path,
    manifest: MissionSetManifest,
) -> tuple[dict[str, MissionEvalCase], list[ValidationIssue]]:
    '''读取并校验 manifest 声明的 case 文件。'''

    cases: dict[str, MissionEvalCase] = {}
    issues: list[ValidationIssue] = []

    for case_file in manifest.case_files:
        case_path = split_dir / case_file
        if not case_path.exists():
            issues.append(ValidationIssue(case_path, 'case file declared in manifest is missing'))
            continue

        try:
            case = MissionEvalCase.model_validate_json(case_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            issues.append(ValidationIssue(case_path, f'invalid MissionEvalCase: {exc}'))
            continue

        if case.case_id in cases:
            issues.append(ValidationIssue(case_path, f'duplicate case_id: {case.case_id}'))
            continue

        expected_name = f'{case.case_id}.json'
        if case_path.name != expected_name:
            issues.append(ValidationIssue(case_path, f'file name should be {expected_name}'))

        cases[case.case_id] = case

    return cases, issues


def validate_manifest_consistency(
    manifest_path: Path,
    split: str,
    manifest: MissionSetManifest,
    cases: dict[str, MissionEvalCase],
) -> list[ValidationIssue]:
    '''校验 manifest 与其声明 case 的一致性。'''

    issues: list[ValidationIssue] = []

    if manifest.split != split:
        issues.append(
            ValidationIssue(manifest_path, f'manifest split is {manifest.split!r}, expected {split!r}')
        )

    if manifest.case_count != len(manifest.case_files):
        issues.append(
            ValidationIssue(
                manifest_path,
                f'case_count is {manifest.case_count}, but case_files has {len(manifest.case_files)}',
            )
        )

    if manifest.case_count != len(cases):
        issues.append(
            ValidationIssue(
                manifest_path,
                f'case_count is {manifest.case_count}, but {len(cases)} valid cases loaded',
            )
        )

    return issues


def validate_case_set_consistency(
    split_dir: Path,
    split: str,
    cases: dict[str, MissionEvalCase],
) -> list[ValidationIssue]:
    '''校验同一 split 内的 case 命名、标签与任务族一致性。'''

    issues: list[ValidationIssue] = []
    families: dict[str, list[MissionEvalCase]] = {}

    for case in cases.values():
        case_path = split_dir / f'{case.case_id}.json'
        issues.extend(validate_case_metadata(case_path, split, case))
        family_id = case.case_id.rsplit('-', maxsplit=1)[0]
        families.setdefault(family_id, []).append(case)

    for family_id, family_cases in sorted(families.items()):
        issues.extend(validate_family(split_dir, split, family_id, family_cases))

    return issues


def validate_case_metadata(path: Path, split: str, case: MissionEvalCase) -> list[ValidationIssue]:
    '''校验单个 case 的文件名派生元数据。'''

    issues: list[ValidationIssue] = []
    variant = case.case_id.rsplit('-', maxsplit=1)[-1]
    expected_variant_tag = VARIANT_TAGS.get(variant)

    if split not in case.tags:
        issues.append(ValidationIssue(path, f'missing split tag: {split}'))

    template_tag = case.case_id.removeprefix('DEV-').rsplit('-', maxsplit=1)[0]
    if template_tag not in case.tags:
        issues.append(ValidationIssue(path, f'missing template tag: {template_tag}'))

    if expected_variant_tag is not None and expected_variant_tag not in case.tags:
        issues.append(ValidationIssue(path, f'missing variant tag: {expected_variant_tag}'))

    return issues


def validate_family(
    split_dir: Path,
    split: str,
    family_id: str,
    family_cases: list[MissionEvalCase],
) -> list[ValidationIssue]:
    '''校验同一任务族内 N/C/F 变体的关系。'''

    issues: list[ValidationIssue] = []
    variants = sorted(case.case_id.rsplit('-', maxsplit=1)[-1] for case in family_cases)

    if split == 'dev' and tuple(variants) != REQUIRED_DEV_VARIANTS:
        issues.append(
            ValidationIssue(
                split_dir / family_id,
                f'dev family variants are {variants}, expected {list(REQUIRED_DEV_VARIANTS)}',
            )
        )

    instructions = {case.instruction for case in family_cases}
    if len(instructions) != 1:
        issues.append(
            ValidationIssue(
                split_dir / family_id,
                'family N/C/F cases must share the same instruction text',
            )
        )

    return issues


def print_report(issues: list[ValidationIssue]) -> None:
    '''输出校验错误报告。'''

    print(f'FAILED: found {len(issues)} mission set issue(s)')
    for issue in issues:
        print(f'- {issue.path}: {issue.message}')


if __name__ == '__main__':
    main()
