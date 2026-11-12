add_library(ViewerPlugin SHARED
    ViewerPlugin.h
    ViewerPlugin.cpp
    ViewerRibbonUI.h
    ViewerRibbonUI.cpp
    ViewerNavigatorUI.h
    ViewerNavigatorUI.cpp
    ViewerListUI.h
    ViewerListUI.cpp
    ViewerViewModel.h
    ViewerViewModel.cpp
)

target_include_directories(ViewerPlugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${PROJECT_SOURCE_DIR}/src/include
    ${PROJECT_SOURCE_DIR}/src/utils
)

target_precompile_headers(ViewerPlugin REUSE_FROM SharedPCHLight)
target_link_libraries(ViewerPlugin PRIVATE SharedPCHLight)

target_link_libraries(ViewerPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        AppCore # Import core/utils ABI (SignalBus, UserManager, IconFactory, ...)
)

# Install plugin to a 'plugins' directory in the build folder
set_target_properties(ViewerPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
