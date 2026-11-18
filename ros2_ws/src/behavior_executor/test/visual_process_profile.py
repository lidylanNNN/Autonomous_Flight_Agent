'''只记录视觉服务实际回调，不替换输入、对象或返回值。'''
from __future__ import annotations

import json
import runpy
import sys
import threading
from pathlib import Path
from types import FrameType


def test_temp_main() -> None:
    '''由各服务对应的 Python 环境运行安装入口并归档回调。'''
    test_temp_directory, test_temp_role, test_temp_executable = sys.argv[1:4]
    if test_temp_role == 'capture':
        from flight_agent_ros.capture_photo_server import CapturePhotoServer
        test_temp_code = CapturePhotoServer.capture.__code__
        test_temp_filename = 'test_temp_service_calls.jsonl'
    else:
        from flight_agent_ros.inspect_image_server import InspectImageServer
        test_temp_code = InspectImageServer.inspect.__code__
        test_temp_filename = 'test_temp_inspect_calls.jsonl'

    def test_temp_record(frame: FrameType, event: str, arg: object) -> None:
        '''回调返回时记录原样请求和结果。'''
        if frame.f_code is test_temp_code and event == 'return' and arg is not None:
            request = frame.f_locals['request']
            with (Path(test_temp_directory) / test_temp_filename).open('a') as test_temp_log:
                test_temp_log.write(json.dumps({'input': request.input, 'prev_output': request.prev_output,
                    'success': arg.success, 'output': arg.output}) + '\n')

    sys.argv = [test_temp_executable, *sys.argv[4:]]
    sys.setprofile(test_temp_record)
    threading.setprofile(test_temp_record)
    try:
        runpy.run_path(test_temp_executable, run_name='__main__')
    finally:
        sys.setprofile(None)
        threading.setprofile(None)


if __name__ == '__main__':
    test_temp_main()
