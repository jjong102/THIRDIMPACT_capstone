cmake_minimum_required(VERSION 3.5)
project(mapping_pkg)

# Default to C99
if(NOT CMAKE_C_STANDARD)
  set(CMAKE_C_STANDARD 99)
endif()

# Default to C++14
if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 14)
endif()

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)

# Install launch files
install(
  DIRECTORY launch config
  DESTINATION share/${PROJECT_NAME}
)
install(
  DIRECTORY scripts
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
