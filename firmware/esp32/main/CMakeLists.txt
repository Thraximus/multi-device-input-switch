idf_component_register(SRCS "main.c" "oled.c"
                       INCLUDE_DIRS "." "../../src"
                       REQUIRES bt esp_driver_uart esp_driver_i2c nvs_flash esp_timer)
include(${CMAKE_CURRENT_LIST_DIR}/../../settings.cmake)
target_compile_definitions(${COMPONENT_LIB} PRIVATE ${SWITCHER_FIRMWARE_DEFINITIONS})
