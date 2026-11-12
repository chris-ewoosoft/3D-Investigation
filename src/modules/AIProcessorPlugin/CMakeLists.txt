

add_library(AIProcessorPlugin SHARED
    AIProcessorPlugin.h
    AIProcessorPlugin.cpp
    AITrainDockWidget.h
    AITrainDockWidget.cpp
    AIProcessorRibbonUI.h
    AIProcessorRibbonUI.cpp
    VideoTrackerThread.h
    VideoTrackerThread.cpp
)

target_include_directories(AIProcessorPlugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${PROJECT_SOURCE_DIR}/src/include
    ${PROJECT_SOURCE_DIR}/src/utils
    ${OpenCV_INCLUDE_DIRS}
)

target_precompile_headers(AIProcessorPlugin REUSE_FROM SharedPCH)
target_link_libraries(AIProcessorPlugin PRIVATE SharedPCH)

target_link_libraries(AIProcessorPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        Qt6::Concurrent
        ${OpenCV_LIBS}
        AppCore
)

set_target_properties(AIProcessorPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
