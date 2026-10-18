

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
)

# Light PCH + light ABI: ReconstructionPlugin orchestrate qua
# IReconstructionService (PointCloudData ABI-neutral) — không parse hay link
# VTK/OpenCV/PCL trực tiếp (enforced by tests/CheckArchitectureBoundaries.cmake).
target_precompile_headers(ReconstructionPlugin REUSE_FROM SharedPCHLight)
target_link_libraries(ReconstructionPlugin PRIVATE SharedPCHLight)

target_link_libraries(ReconstructionPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        AppCore
)

set_target_properties(ReconstructionPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
