'''读取正式拍照产物并执行有期限的本地人物检测。'''
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rosllm_srvs.srv import AtomicAction

_COEFFICIENTS_SHA256 = 'cb2198952eaa5bc7e43d950b9f2aa1966528063c7295c7262133e7fa0d3d564c'
_PARAMETERS = {'hitThreshold': 0, 'winStride': [8, 8], 'padding': [8, 8], 'scale': 1.05,
               'groupThreshold': 2, 'useMeanshiftGrouping': False}
_MEDIA_FIELDS = ('photo_id', 'image_path', 'metadata_path', 'sha256')
_BUDGET_S = 5.0


class InspectError(RuntimeError):
    '''携带契约失败码，不交付部分检测结果。'''


def main() -> None:
    '''生产入口创建节点；内部子进程模式执行一次文件核对与检测。'''
    parser = argparse.ArgumentParser(description='检查上一项拍照图片中的人物')
    parser.add_argument('--photo-root', type=Path, default=Path('artifacts/photos'))
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    arguments, ros_arguments = parser.parse_known_args()
    if arguments.worker:
        try:
            result = inspect_photo(arguments.photo_root, json.load(sys.stdin))
        except InspectError as error:
            result = {'error_code': str(error)}
        print(json.dumps(result, allow_nan=False), flush=True)
        return
    rclpy.init(args=ros_arguments)
    node = None
    executor = MultiThreadedExecutor(num_threads=2)
    try:
        node = InspectImageServer(arguments.photo_root)
        executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


class InspectImageServer(Node):
    '''持有照片根目录和并发锁，每次只运行一个检测子进程。'''

    def __init__(self, photo_root: Path) -> None:
        '''先检查目录和工具，依赖无效时不发布服务。'''
        self.photo_root = photo_root.resolve(strict=True)
        if not self.photo_root.is_dir():
            raise ValueError('照片根目录必须是目录')
        next(self.photo_root.iterdir(), None)
        detector, self.tool = create_detector()
        del detector
        self._busy = Lock()
        self._worker: subprocess.Popen[str] | None = None
        super().__init__('inspect_image_server')
        self.create_service(AtomicAction, 'inspect_image', self.inspect,
                            callback_group=ReentrantCallbackGroup())

    def inspect(self, request: AtomicAction.Request,
                response: AtomicAction.Response) -> AtomicAction.Response:
        '''验证请求并运行子进程；未检出成功返回，故障返回空引用。'''
        started = time.monotonic()
        output: dict[str, Any] = dict.fromkeys((*_MEDIA_FIELDS, 'target_class', 'tool',
                                               'detections', 'detection_cnt', 'error_code'))
        output.update(source='inspect_image_server', message='')
        acquired = False
        try:
            try:
                if json.loads(request.input) != {'target_class': 'person'}:
                    raise ValueError('只支持 target_class=person')
            except (ValueError, TypeError) as error:
                raise InspectError('INVALID_INPUT') from error
            output['target_class'] = 'person'
            reference = parse_reference(request.prev_output)
            acquired = self._busy.acquire(blocking=False)
            if not acquired or (self._worker is not None and self._worker.poll() is None):
                raise InspectError('BUSY')
            result = self.run_worker(reference)
            if result.get('error_code'):
                raise InspectError(result['error_code'])
            output.update(result)
            output['message'] = '检测完成' if result['detection_cnt'] else '该图片未检出人物'
        except InspectError as error:
            output['error_code'], output['message'] = str(error), f'检测失败：{error}'
        except Exception as error:  # noqa: BLE001 -- ROS 服务边界统一返回失败
            output['error_code'], output['message'] = 'SERVICE_ERROR', str(error)
        finally:
            if acquired:
                self._busy.release()
        response.success = output['error_code'] is None
        response.output = json.dumps(output, ensure_ascii=False, allow_nan=False)
        self.get_logger().info(f'photo_id={output["photo_id"]} tool={self.tool} '
                               f'elapsed_s={time.monotonic()-started:.3f} '
                               f'error_code={output["error_code"]}')
        return response

    def run_worker(self, reference: dict[str, Any]) -> dict[str, Any]:
        '''启动并限时等待一次检测；超时或异常均终止并回收子进程。'''
        deadline = time.monotonic() + _BUDGET_S
        try:
            self._worker = subprocess.Popen(
                [sys.executable, '-m', __name__, '--worker', '--photo-root', str(self.photo_root)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as error:
            raise InspectError('DETECTOR_UNAVAILABLE') from error
        try:
            stdout, stderr = self._worker.communicate(
                json.dumps(reference), timeout=max(0, deadline-time.monotonic()))
            if time.monotonic() >= deadline:
                raise InspectError('DETECTION_TIMEOUT')
            if self._worker.returncode:
                self.get_logger().error(stderr)
                raise InspectError('DETECTION_FAILED')
            return json.loads(stdout)
        except subprocess.TimeoutExpired as error:
            raise InspectError('DETECTION_TIMEOUT') from error
        except (ValueError, TypeError) as error:
            raise InspectError('DETECTION_FAILED') from error
        finally:
            if self._worker.poll() is None:
                self._worker.kill()
                try:
                    self._worker.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    self.get_logger().error('检测子进程未能回收，服务保持忙状态，请停止服务')


def parse_reference(text: str) -> dict[str, Any]:
    '''只接受拍照成功响应的引用，不搜索历史图片。'''
    try:
        reference = json.loads(text)
        if reference['source'] != 'capture_photo_server' or reference['error_code'] is not None:
            raise ValueError('不是拍照成功响应')
        if not all(isinstance(reference[key], str) and reference[key] for key in _MEDIA_FIELDS):
            raise ValueError('缺少媒体引用')
        if str(UUID(reference['photo_id'])) != reference['photo_id']:
            raise ValueError('无效照片 UUID')
        if (len(reference['sha256']) != 64 or
                any(value not in '0123456789abcdefABCDEF' for value in reference['sha256'])):
            raise ValueError('无效哈希')
        if not all(Path(reference[key]).is_absolute() for key in ('image_path', 'metadata_path')):
            raise ValueError('路径必须是绝对路径')
        return reference
    except (ValueError, TypeError, KeyError) as error:
        raise InspectError('INVALID_PREV_OUTPUT') from error


def create_detector() -> tuple[Any, dict[str, Any]]:
    '''构建已冻结的真实 CPU 检测器并核对版本及内置系数。'''
    try:
        import cv2
        cv2.setNumThreads(1)
        coefficients = cv2.HOGDescriptor_getDefaultPeopleDetector()
        digest = hashlib.sha256(coefficients.tobytes()).hexdigest()
        if cv2.__version__ != '4.6.0' or digest != _COEFFICIENTS_SHA256:
            raise ValueError('工具版本或系数与 T01c 不一致')
        detector = cv2.HOGDescriptor()
        detector.setSVMDetector(coefficients)
        return detector, {'name': 'opencv_hog_default_people', 'opencv_version': cv2.__version__,
                          'coefficients_sha256': digest, 'device': 'CPU', 'opencv_thread_cnt': 1,
                          'parameters': _PARAMETERS}
    except Exception as error:
        raise InspectError('DETECTOR_UNAVAILABLE') from error


def inspect_photo(photo_root: Path, reference: dict[str, Any]) -> dict[str, Any]:
    '''核对同一份 PNG 字节后调用真实 HOG，保留原始框和分数。'''
    import numpy as np
    from PIL import Image
    try:
        root = photo_root.resolve(strict=True)
        directory = root / reference['photo_id']
        for key, filename in (('image_path', 'image.png'), ('metadata_path', 'metadata.json')):
            path = Path(reference[key]).resolve(strict=True)
            if not path.is_relative_to(root) or path != directory / filename:
                raise ValueError('图片引用不在指定照片目录')
        metadata = json.loads(Path(reference['metadata_path']).read_text())
        if (type(metadata['schema_version']) is not int or metadata['schema_version'] != 1 or
                any(metadata[key] != reference[key] for key in ('photo_id', 'image_path', 'sha256'))):
            raise ValueError('元数据与引用不对应')
        pixels = Path(reference['image_path']).read_bytes()
        if hashlib.sha256(pixels).hexdigest() != reference['sha256']:
            raise ValueError('图片哈希不符')
        with Image.open(BytesIO(pixels)) as image:
            if (image.format != 'PNG' or image.mode != 'RGB' or image.size != (1280, 960) or
                    (metadata['image']['width'], metadata['image']['height']) != image.size):
                raise ValueError('图片格式或尺寸不符')
            bgr = np.asarray(image)[:, :, ::-1].copy()
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise InspectError('MEDIA_INVALID') from error
    detector, tool = create_detector()
    try:
        boxes, weights = detector.detectMultiScale(bgr, **_PARAMETERS)
        if len(boxes) != len(weights):
            raise ValueError('框与分数数量不一致')
        detections = []
        for box, weight in zip(boxes, weights):
            x, y, w, h = (int(value) for value in box)
            if not (x >= 0 and y >= 0 and w > 0 and h > 0 and x+w <= 1280 and y+h <= 960
                    and math.isfinite(float(weight))):
                raise ValueError('检测框或分数无效')
            detections.append({'class': 'person', 'xywh': [x, y, w, h], 'svm_score': float(weight)})
        return {key: reference[key] for key in _MEDIA_FIELDS} | {
            'tool': tool, 'detections': detections, 'detection_cnt': len(detections)}
    except Exception as error:
        raise InspectError('DETECTION_FAILED') from error


if __name__ == '__main__':
    main()
