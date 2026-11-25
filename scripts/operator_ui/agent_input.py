'''以 JSON 行承载浏览器原文，复用实际 ros_agent_srv 主循环。'''
import json
import os
import sys

from agent_comm.ros_agent_srv import main


def read_human(prompt: str) -> str:
    '''在独立管道报告等待状态，完整读取含换行的用户输入。'''
    print(prompt, flush=True)
    os.write(int(os.environ['AFA_UI_READY_FD']), b'R')
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    value = json.loads(line)
    if not isinstance(value, str):
        raise TypeError('任务必须为文本')
    return value


if __name__ == '__main__':
    main(input_reader=read_human)
