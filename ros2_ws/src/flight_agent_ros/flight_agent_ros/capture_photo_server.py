'''通过实际相机消息完成单次拍照，不发送飞行指令。'''
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
from collections import deque
from functools import partial
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Condition, Lock
from typing import Any
from uuid import uuid4

import rclpy
from PIL import Image as PngImage
from px4_msgs.msg import VehicleLocalPosition
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
from rosgraph_msgs.msg import Clock
from rosidl_runtime_py.convert import message_to_ordereddict
from rosllm_srvs.srv import AtomicAction
from sensor_msgs.msg import CameraInfo, Image

_ERROR_MESSAGES = {
    'INVALID_INPUT': '拍照参数必须是空 JSON 对象',
    'BUSY': '上一张照片仍在处理，请稍后再试',
    'CLOCK_UNAVAILABLE': '尚未收到有效且持续前进的仿真时钟',
    'CLOCK_RESET': '仿真时钟发生回退，请重启拍照服务',
    'FRAME_TIMEOUT': '5 秒内没有收到符合要求的新图片和相机参数',
    'POSITION_UNAVAILABLE': '飞机位置缺失、过期或无效',
    'SAVE_FAILED': '照片或来源信息未能完整保存和核对',
}


class CaptureError(RuntimeError):
    '''携带拍照契约规定的失败码。'''


def main() -> None:
    '''创建拍照节点；等待请求时持续接收相机、位置和时钟。'''
    parser = argparse.ArgumentParser(description='保存本次请求之后的新照片及来源信息')
    parser.add_argument('--output-dir', type=Path, default=Path('artifacts/photos'))
    arguments, ros_arguments = parser.parse_known_args()
    rclpy.init(args=ros_arguments)
    node = None
    executor = MultiThreadedExecutor(num_threads=3)
    try:
        node = CapturePhotoServer(arguments.output_dir)
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


class CapturePhotoServer(Node):
    '''持有有限消息缓存；每次只处理一个拍照请求。'''

    def __init__(self, output_dir: Path) -> None:
        '''先检查输出目录，再注册正式服务和消息订阅。'''
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=self.output_dir):
            pass
        super().__init__('capture_photo_server', parameter_overrides=[
            Parameter('use_sim_time', value=True)])
        self._condition, self._busy = Condition(), Lock()
        self._images: deque[tuple[Image, int]] = deque(maxlen=20)
        self._infos: dict[int, CameraInfo] = {}
        self._position: tuple[VehicleLocalPosition, int] | None = None
        self._clock = self._progress = self._highest = 0
        self._clock_reset_detected = False
        self._process_id = str(uuid4())
        callbacks = MutuallyExclusiveCallbackGroup()
        for message_type, topic, kind in (
            (Image, '/camera/image_raw', 'image'),
            (CameraInfo, '/camera/camera_info', 'info'),
            (VehicleLocalPosition, '/fmu/out/vehicle_local_position', 'position'),
        ):
            self.create_subscription(message_type, topic, partial(self.receive, kind),
                                     qos_profile_sensor_data, callback_group=callbacks)
        self.create_subscription(Clock, '/clock', self.receive_clock,
                                 QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT),
                                 callback_group=callbacks)
        self.create_service(AtomicAction, 'capture_photo', self.capture,
                            callback_group=ReentrantCallbackGroup())

    def capture(self, request: AtomicAction.Request,
                response: AtomicAction.Response) -> AtomicAction.Response:
        '''检查请求，等待新图并保存；任何失败均不交付文件引用。'''
        started = time.monotonic_ns()
        output = {'source': 'capture_photo_server', 'photo_id': None, 'image_path': None,
                  'metadata_path': None, 'sha256': None, 'error_code': None, 'message': ''}
        acquired = False
        try:
            try:
                if json.loads(request.input) != {}:
                    raise ValueError('拍照参数必须为空对象')
            except (ValueError, TypeError) as error:
                raise CaptureError('INVALID_INPUT') from error
            acquired = self._busy.acquire(blocking=False)
            if not acquired:
                raise CaptureError('BUSY')
            with self._condition:
                if self._clock_reset_detected:
                    raise CaptureError('CLOCK_RESET')
                if not self.clock_ready(time.monotonic_ns()):
                    raise CaptureError('CLOCK_UNAVAILABLE')
                baseline = max(self._clock, self._highest)
                image, info, timing, position = self.wait_frame(started, baseline)
            try:
                output.update(self.save(image, info, timing, position))
            except CaptureError:
                raise
            except Exception as error:
                self.get_logger().error(f'保存照片失败: {error}')
                raise CaptureError('SAVE_FAILED') from error
            output['message'] = '照片和来源信息已保存'
        except CaptureError as error:
            output['error_code'] = str(error)
            output['message'] = _ERROR_MESSAGES.get(str(error), '拍照未完成')
        except Exception as error:  # noqa: BLE001 -- ROS 回调边界必须返回契约失败响应
            output['error_code'], output['message'] = 'SERVICE_ERROR', str(error)
            self.get_logger().error(f'拍照内部异常: {error}')
        finally:
            if acquired:
                self._busy.release()
        response.success = output['error_code'] is None
        response.output = json.dumps(output, ensure_ascii=False, allow_nan=False)
        return response

    def wait_frame(self, started: int, baseline: int) -> tuple[
            Image, CameraInfo, dict[str, int | str], VehicleLocalPosition]:
        '''在条件锁内等候新帧；等待时释放锁，让订阅回调继续更新数据。'''
        while rclpy.ok():
            now = time.monotonic_ns()
            if self._clock_reset_detected:
                raise CaptureError('CLOCK_RESET')
            if now - started >= 5_000_000_000:
                break
            for image, received in self._images:
                info = self._infos.get(stamp_ns(image))
                if (received <= started or stamp_ns(image) <= baseline or
                        not self.clock_ready(now) or not 0 <= now - received <= 500_000_000 or
                        not 0 <= self._clock - stamp_ns(image) <= 500_000_000 or
                        not valid_frame(image, info)):
                    continue
                if self._position is None:
                    raise CaptureError('POSITION_UNAVAILABLE')
                position, position_received = self._position
                if (not 0 <= now - position_received <= 500_000_000 or
                        not position.xy_valid or not position.z_valid or
                        not all(isfinite(value) for value in (position.x, position.y, position.z))):
                    raise CaptureError('POSITION_UNAVAILABLE')
                timing = {'clock_ns': self._clock, 'request_monotonic_ns': started,
                          'image_received_monotonic_ns': received, 'selected_monotonic_ns': now,
                          'position_received_monotonic_ns': position_received,
                          'process_id': self._process_id}
                return image, info, timing, position
            self._condition.wait(timeout=min(0.05, (5_000_000_000 - now + started) / 1e9))
        raise CaptureError('FRAME_TIMEOUT')

    def clock_ready(self, now: int) -> bool:
        '''仿真时钟必须非零，且在最近一秒内前进过。'''
        return self._clock > 0 and 0 <= now - self._progress <= 1_000_000_000

    def receive(self, kind: str, message: Image | CameraInfo | VehicleLocalPosition) -> None:
        '''记录消息及本地接收时间，不允许回退后继续填充缓存。'''
        with self._condition:
            if self._clock_reset_detected:
                return
            if kind == 'image':
                self._images.append((message, time.monotonic_ns()))
                self._highest = max(self._highest, stamp_ns(message))
            elif kind == 'info':
                self._infos[stamp_ns(message)] = message
                if len(self._infos) > 120:
                    del self._infos[next(iter(self._infos))]
            else:
                self._position = message, time.monotonic_ns()
            self._condition.notify_all()

    def receive_clock(self, message: Clock) -> None:
        '''已观察到时钟回退后清空缓存并锁定，直到重启服务。'''
        with self._condition:
            clock = message.clock.sec * 10**9 + message.clock.nanosec
            if clock < self._clock:
                self._clock_reset_detected = True
                self._images.clear()
                self._infos.clear()
                self._position = None
            if clock > self._clock:
                self._progress = time.monotonic_ns()
            self._clock = clock
            self._condition.notify_all()

    def save(self, image: Image, info: CameraInfo, timing: dict[str, int | str],
             position: VehicleLocalPosition) -> dict[str, str]:
        '''写入临时目录并重新核对；确认未回退后才交付完整目录。'''
        photo_id = str(uuid4())
        final = self.output_dir / photo_id
        temporary = self.output_dir / f'.tmp-{photo_id}'
        temporary.mkdir()
        try:
            pixels = bytes(image.data)
            PngImage.frombytes('RGB', (image.width, image.height), pixels).save(temporary/'image.png')
            sha256 = hashlib.sha256((temporary/'image.png').read_bytes()).hexdigest()
            result = {'photo_id': photo_id, 'image_path': str(final/'image.png'),
                      'metadata_path': str(final/'metadata.json'), 'sha256': sha256}
            metadata = {'schema_version': 1, 'photo_id': photo_id, 'image_path': result['image_path'],
                        'sha256': sha256, 'timing': timing, 'vehicle_position': position_metadata(position),
                        'image': {'source_topic': '/camera/image_raw', 'stamp_ns': stamp_ns(image)},
                        'camera_info': {'source_topic': '/camera/camera_info',
                                        **message_to_ordereddict(info)}}
            metadata['image'].update(frame_id=image.header.frame_id, **{
                key: getattr(image, key) for key in ('width', 'height', 'encoding', 'step')})
            metadata_path = temporary/'metadata.json'
            metadata_path.write_text(json.dumps(metadata, allow_nan=False), encoding='utf-8')
            with PngImage.open(temporary/'image.png') as saved:
                if saved.mode != 'RGB' or saved.size != (image.width, image.height) or saved.tobytes() != pixels:
                    raise ValueError('PNG 内容不对应')
            if (json.loads(metadata_path.read_text()) != metadata or
                    hashlib.sha256((temporary/'image.png').read_bytes()).hexdigest() != sha256):
                raise ValueError('来源信息不对应')
            with self._condition:
                if self._clock_reset_detected:
                    raise CaptureError('CLOCK_RESET')
                if final.exists():
                    raise FileExistsError(final)
                temporary.rename(final)
            return result
        finally:
            if temporary.exists():
                try:
                    shutil.rmtree(temporary)
                except OSError as error:
                    self.get_logger().error(f'无法清理临时照片 {temporary}: {error}')


def stamp_ns(message: Image | CameraInfo) -> int:
    '''读取相机消息的源仿真时间，单位纳秒。'''
    return message.header.stamp.sec * 10**9 + message.header.stamp.nanosec


def valid_frame(image: Image, info: CameraInfo | None) -> bool:
    '''检查冻结相机的图像布局、参数对应及标定数字。'''
    return bool(info is not None and stamp_ns(image) > 0 and
                stamp_ns(image) == stamp_ns(info) and image.header.frame_id and
                image.header.frame_id == info.header.frame_id and
                image.width == info.width == 1280 and image.height == info.height == 960 and
                image.encoding == 'rgb8' and image.step == 3840 and len(image.data) == 3686400 and
                all(isfinite(value) for value in (*info.k, *info.p, *info.r, *info.d)) and
                info.k[0] > 0 and info.k[4] > 0 and info.k[8] == 1 and
                info.p[0] > 0 and info.p[5] > 0)


def position_metadata(position: VehicleLocalPosition) -> dict[str, Any]:
    '''保存原始 PX4 位置及来源；不可用的全局参考使用 null。'''
    fields = ['timestamp', 'timestamp_sample', 'x', 'y', 'z', 'xy_valid', 'z_valid', 'ref_timestamp',
              'ref_lat', 'ref_lon', 'ref_alt', 'xy_global', 'z_global', 'xy_reset_counter',
              'z_reset_counter']
    result = dict(source_topic='/fmu/out/vehicle_local_position',
                  source_type='px4_msgs/msg/VehicleLocalPosition',
                  **{key: getattr(position, key) for key in fields})
    for key, valid in (('ref_lat', position.xy_global), ('ref_lon', position.xy_global),
                       ('ref_alt', position.z_global)):
        if not valid or not isfinite(result[key]):
            result[key] = None
    return result


if __name__ == '__main__':
    main()
