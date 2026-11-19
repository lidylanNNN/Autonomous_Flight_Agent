'''只记录视觉服务实际回调，不替换输入、对象或返回值。'''
from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path


def test_temp_main() -> None:
    '''由各服务对应的 Python 环境运行安装入口并归档回调。'''
    test_temp_directory, test_temp_role, test_temp_executable = sys.argv[1:4]
    if test_temp_role == 'capture':
        from flight_agent_ros.capture_photo_server import CapturePhotoServer
        test_temp_class, test_temp_method = CapturePhotoServer, 'capture'
        test_temp_filename = 'test_temp_service_calls.jsonl'
    else:
        from flight_agent_ros.inspect_image_server import InspectImageServer
        test_temp_class, test_temp_method = InspectImageServer, 'inspect'
        test_temp_filename = 'test_temp_inspect_calls.jsonl'
    test_temp_original = getattr(test_temp_class, test_temp_method)

    def test_temp_record(self, request, response):
        """只在服务返回后记录一次，执行原业务方法并原样返回。"""
        result = test_temp_original(self, request, response)
        with (Path(test_temp_directory) / test_temp_filename).open('a') as test_temp_log:
            test_temp_log.write(json.dumps({'input': request.input, 'prev_output': request.prev_output,
                'success': result.success, 'output': result.output}) + '\n')
        return result

    setattr(test_temp_class, test_temp_method, test_temp_record)
    sys.argv = [test_temp_executable, *sys.argv[4:]]
    try:
        runpy.run_path(test_temp_executable, run_name='__main__')
    finally:
        setattr(test_temp_class, test_temp_method, test_temp_original)


if __name__ == '__main__':
    test_temp_main()
