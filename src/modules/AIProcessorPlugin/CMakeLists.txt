

add_library(AIProcessorPlugin SHARED
    AIProcessorPlugin.h
    AIProcessorPlugin.cpp
    AIPredictionLogger.h
    AIPredictionLogger.cpp
    AITrainDockWidget.h
    AITrainDockWidget.cpp
    AIProcessorRibbonUI.h
    AIProcessorRibbonUI.cpp
    VideoTrackerThread.h
    VideoTrackerThread.cpp
)

target_include_directories(AIProcessorPlugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${PROJECT_SOURCE_DIR}/src/core
    ${PROJECT_SOURCE_DIR}/src/utils
    ${VTK_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
    ${onnxruntime_SOURCE_DIR}/include
)

target_precompile_headers(AIProcessorPlugin REUSE_FROM SharedPCH)
target_link_libraries(AIProcessorPlugin PRIVATE SharedPCH)

target_link_libraries(AIProcessorPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        Qt6::Concurrent
        ${VTK_LIBRARIES}
        ${OpenCV_LIBS}
        ${onnxruntime_SOURCE_DIR}/lib/onnxruntime.lib
        AppCore
)

set_target_properties(AIProcessorPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
