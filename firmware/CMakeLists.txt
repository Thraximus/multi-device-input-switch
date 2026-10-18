cmake_minimum_required(VERSION 3.13)

set(PICO_BOARD pico)
set(PICO_SDK_PATH ${CMAKE_CURRENT_LIST_DIR}/pico-sdk)
include(${PICO_SDK_PATH}/pico_sdk_init.cmake)

project(switcher_hub C CXX ASM)
include(${CMAKE_CURRENT_LIST_DIR}/settings.cmake)
set(CMAKE_C_STANDARD 11)
pico_sdk_init()

add_executable(switcher_hub
    src/main.c
    src/usb_descriptors.c
    src/xinput.c
)
target_include_directories(switcher_hub PRIVATE src)
target_compile_options(switcher_hub PRIVATE -Wall -Wextra)
target_compile_definitions(switcher_hub PRIVATE ${SWITCHER_FIRMWARE_DEFINITIONS})
target_link_libraries(switcher_hub PRIVATE
    pico_stdlib
    pico_unique_id
    hardware_uart
    hardware_irq
    pico_bootrom
    tinyusb_device
    tinyusb_board
)
pico_add_extra_outputs(switcher_hub)
