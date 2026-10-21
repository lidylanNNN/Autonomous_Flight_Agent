from setuptools import find_packages, setup

package_name = 'agent_comm'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/config', ['config/atomic_action_library.yaml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'ros_agent_srv = agent_comm.ros_agent_srv:main',
            'llm_node = agent_comm.llm_node:main',
        ],
    },
)
