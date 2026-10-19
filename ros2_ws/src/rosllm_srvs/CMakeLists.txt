cmake_minimum_required(VERSION 3.8)
project(rosllm_srvs)

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "srv/Observation.srv"
  "srv/LLM.srv"
  "srv/ExecuteBehavior.srv"
  "srv/AtomicAction.srv"
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
