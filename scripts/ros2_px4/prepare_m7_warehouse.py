#!/usr/bin/python3
'''按固定清单准备和校验 M7 仓库；仅 --fetch 允许下载。'''
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = PROJECT_ROOT / 'configs/m7/warehouse_manifest.json'


def main() -> None:
    '''校验缓存或显式获取资源，向 stdout 返回启动器所需 JSON。'''
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    try:
        manifest = json.loads(MANIFEST_PATH.read_text())
        cache_root = Path(os.environ.get('M7_WORLD_CACHE_ROOT',
                                       PROJECT_ROOT / 'artifacts/m7_world_cache')).resolve()
        cache = cache_root / manifest['sources']['source']['commit']
        px4 = Path(os.environ.get('PX4_ROOT', Path.home() / 'PX4-Autopilot')).resolve()
        if not cache.exists():
            if not args.fetch:
                raise ValueError(f'Missing warehouse cache: {cache}; run with --fetch first')
            cache_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.warehouse-', dir=cache_root) as stage:
                staged = Path(stage) / 'cache'
                staged.mkdir()
                with ThreadPoolExecutor(max_workers=6) as pool:
                    list(pool.map(partial(download_file, staged, manifest['sources']),
                                  manifest['files'].items(), timeout=120))
                verify_files(staged, manifest)
                world, prepared = prepare_world(staged, manifest, px4)
                (staged / 'warehouse_px4.sdf').write_bytes(world)
                (staged / 'prepared.json').write_text(json.dumps(prepared, indent=2) + '\n')
                staged.rename(cache)
        verify_files(cache, manifest)
        world, prepared = prepare_world(cache, manifest, px4)
        if (cache / 'warehouse_px4.sdf').read_bytes() != world:
            raise ValueError(f'Adapted world mismatch: {cache / "warehouse_px4.sdf"}')
        if json.loads((cache / 'prepared.json').read_text()) != prepared:
            raise ValueError(f'Preparation metadata mismatch: {cache / "prepared.json"}')
        print(json.dumps(dict(prepared, world_path=str(cache / 'warehouse_px4.sdf'),
            resource_path=':'.join(str(x) for x in (cache / manifest['model_directory'],
                px4 / 'Tools/simulation/gz/models', px4 / 'Tools/simulation/gz/worlds',
                PROJECT_ROOT / 'configs/m6/models')),
            required_entities=manifest['required_entities'], spawn_pose=manifest['spawn_pose'])))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, ET.ParseError) as error:
        print(f'Warehouse preparation failed: {error}', file=sys.stderr)
        sys.exit(1)


def download_file(cache: Path, sources: dict[str, Any], item: tuple[str, str]) -> None:
    '''下载清单中固定版本的单个文件，并在写入前核对内容。'''
    name, expected_sha256 = item
    source_name, remote_path = name.split('/', 1)
    source = sources[source_name]
    url = f'https://raw.githubusercontent.com/{source["repository"]}/{source["commit"]}/{remote_path}'
    with urlopen(url, timeout=20) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError(f'Download SHA256 mismatch: {name}')
    target = cache / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def verify_files(cache: Path, manifest: dict[str, Any]) -> None:
    '''检查固定文件及其哈希，拒绝缺失、额外资源或越出缓存的链接。'''
    for name, expected_sha256 in manifest['files'].items():
        path = cache / name
        if not path.resolve().is_relative_to(cache.resolve()):
            raise ValueError(f'Resource escapes cache: {name}')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
            raise ValueError(f'Resource missing or SHA256 mismatch: {name}')
    actual = {str(x.relative_to(cache)) for source in manifest['sources']
              for x in (cache / source).rglob('*') if x.is_file()}
    if actual != set(manifest['files']):
        raise ValueError(f'Unexpected cached resources: {sorted(actual - set(manifest["files"]))}')


def prepare_world(cache: Path, manifest: dict[str, Any], px4: Path) -> tuple[bytes, dict[str, str]]:
    '''核对冻结 PX4、资源依赖并按固定规则生成世界和准备记录。'''
    environment = yaml.safe_load((PROJECT_ROOT / 'manifests/environment.yaml').read_text())
    px4_commit = subprocess.check_output(['git', '-C', str(px4), 'rev-parse', 'HEAD'],
                                        text=True, timeout=5).strip()
    if px4_commit != environment['px4']['commit']:
        raise ValueError(f'PX4 commit differs from frozen environment: {px4_commit}')
    source = cache / manifest['world_file']
    expanded = subprocess.run(['/opt/ros/jazzy/bin/xacro', str(source), 'headless:=false'],
                              capture_output=True, check=True, timeout=20)
    tree = ET.fromstring(expanded.stdout)
    world = tree.find('world')
    if world is None or world.get('name') != manifest['world_name']:
        raise ValueError('Unexpected warehouse world name')
    models = cache / manifest['model_directory']
    for xml in [source, *models.glob('*/model.sdf')]:
        for uri in ET.parse(xml).findall('.//uri'):
            if not (uri.text or '').startswith('model://'):
                raise ValueError(f'Unsupported resource URI in {xml}: {uri.text}')
            if not (models / uri.text.removeprefix('model://')).exists():
                raise ValueError(f'Missing model dependency: {uri.text}')
    namespace = {'c': 'http://www.collada.org/2005/11/COLLADASchema'}
    for mesh in models.rglob('*'):
        if mesh.suffix.lower() == '.dae':
            for texture in ET.parse(mesh).findall('.//c:library_images/c:image/c:init_from', namespace):
                if not (mesh.parent / texture.text.replace('\\', '/')).is_file():
                    raise ValueError(f'Missing mesh texture: {mesh}: {texture.text}')
    server_config = px4 / 'src/modules/simulation/gz_bridge/server.config'
    frozen_config = subprocess.check_output(['git', '-C', str(px4), 'show',
        f'{px4_commit}:src/modules/simulation/gz_bridge/server.config'], timeout=5)
    if server_config.read_bytes() != frozen_config:
        raise ValueError('PX4 server.config differs from frozen commit')
    plugins = {x.get('name'): x for x in ET.parse(server_config).findall('.//plugin')}
    existing = {x.get('name') for x in world.findall('plugin')}
    for name in manifest['added_plugins']:
        if name in existing or name not in plugins:
            raise ValueError(f'Unexpected PX4 plugin adaptation: {name}')
        plugin = copy.deepcopy(plugins[name])
        plugin.attrib.pop('entity_name', None)
        plugin.attrib.pop('entity_type', None)
        world.insert(0, plugin)
    data = ET.tostring(tree, encoding='utf-8', xml_declaration=True)
    return data, {'manifest_sha256': hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest(),
        'source_world_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'world_sha256': hashlib.sha256(data).hexdigest(), 'px4_commit': px4_commit,
        'server_config_sha256': hashlib.sha256(server_config.read_bytes()).hexdigest(),
        'world_name': manifest['world_name']}


if __name__ == '__main__':
    main()
