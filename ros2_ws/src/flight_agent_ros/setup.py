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
            'mission_cli = flight_agent_ros.entrypoints.mission_cli:main',
            'px4_world_state_adapter = flight_agent_ros.adapters.px4_world_state_adapter:main',
        ],
    },
)
