add_library(motion_inference STATIC motion_inference.c)
target_include_directories(motion_inference PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
target_compile_features(motion_inference PRIVATE c_std_11)
set(MOTION_MODEL_HEADER "" CACHE FILEPATH "Absolute path to the exported model_data.h")

if(MOTION_MODEL_HEADER)
    if(NOT IS_ABSOLUTE "${MOTION_MODEL_HEADER}" OR NOT EXISTS "${MOTION_MODEL_HEADER}")
        message(FATAL_ERROR "MOTION_MODEL_HEADER must be an existing absolute file path")
    endif()
    configure_file("${MOTION_MODEL_HEADER}" "${CMAKE_CURRENT_BINARY_DIR}/motion_parameters.h" COPYONLY)
    target_include_directories(motion_inference PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
    target_compile_definitions(motion_inference PRIVATE MOTION_HAS_MODEL=1)
    set(nn "${CMAKE_CURRENT_SOURCE_DIR}/../third_party/cmsis-nn")
    target_include_directories(motion_inference PRIVATE "${nn}/Include")
    target_sources(motion_inference PRIVATE
        "${nn}/Source/FullyConnectedFunctions/arm_fully_connected_s8.c"
        "${nn}/Source/FullyConnectedFunctions/arm_fully_connected_get_buffer_sizes_s8.c"
        "${nn}/Source/NNSupportFunctions/arm_nn_vec_mat_mult_t_s8.c")
    target_compile_options(motion_inference PRIVATE -ffp-contract=off -fno-fast-math)
    target_link_libraries(motion_inference PUBLIC m)
endif()
