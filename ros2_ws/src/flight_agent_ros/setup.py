from setuptools import find_packages, setup

package_name = 'flight_agent_ros'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'm3_scripted_flight = flight_agent_ros.entrypoints.m3_scripted_flight:main',
            'px4_world_state_adapter = flight_agent_ros.adapters.px4_world_state_adapter:main',
            'flight_atomic_action_server = flight_agent_ros.flight_atomic_action_server:main',
        ],
    },
)
