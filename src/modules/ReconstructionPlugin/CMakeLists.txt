

add_library(ReconstructionPlugin SHARED
    ReconstructionPlugin.h
    ReconstructionPlugin.cpp
    ReconstructionRibbonUI.h
    ReconstructionRibbonUI.cpp
)

target_include_directories(ReconstructionPlugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${PROJECT_SOURCE_DIR}/src/core
    ${PROJECT_SOURCE_DIR}/src/utils
    ${VTK_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
    ${PCL_INCLUDE_DIRS}
)

target_precompile_headers(ReconstructionPlugin REUSE_FROM SharedPCH)
target_link_libraries(ReconstructionPlugin PRIVATE SharedPCH)

target_link_libraries(ReconstructionPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        ${VTK_LIBRARIES}
        ${OpenCV_LIBS}
        ${PCL_LIBRARIES}
        AppCore
)

set_target_properties(ReconstructionPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
