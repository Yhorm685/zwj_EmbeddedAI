cmake_minimum_required(VERSION 3.22)
set(CMAKE_PROJECT_NAME firmware)
project(firmware LANGUAGES C CXX ASM)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)
add_executable(firmware)
add_subdirectory(cmake/stm32cubemx)
add_subdirectory(inference)
target_sources(firmware PRIVATE app/acquisition.cpp app/console.cpp app/runtime.cpp)
target_include_directories(firmware PRIVATE app)
target_link_libraries(firmware stm32cubemx motion_inference)
add_custom_command(TARGET firmware POST_BUILD
    COMMAND ${CMAKE_OBJCOPY} -O binary $<TARGET_FILE:firmware> firmware.bin
    COMMAND ${CMAKE_OBJCOPY} -O ihex $<TARGET_FILE:firmware> firmware.hex
    COMMAND ${CMAKE_SIZE} $<TARGET_FILE:firmware>
    VERBATIM)
