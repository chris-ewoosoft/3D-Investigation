

add_library(AIAssistantPlugin SHARED
    AIAssistantPlugin.h
    AIAssistantPlugin.cpp
    AIAttachmentPreviewFactory.h
    AIAttachmentPreviewFactory.cpp
    AIAssistantRibbonUI.h
    AIAssistantRibbonUI.cpp
    ChatBotDockWidget.h
    ChatBotDockWidget.cpp
    ChatImageViewer.h
    ChatImageViewer.cpp
    ChatMessageRenderer.h
    ChatMessageRenderer.cpp
    ChatTemplates.h
)

target_include_directories(AIAssistantPlugin PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${PROJECT_SOURCE_DIR}/src/core
    ${PROJECT_SOURCE_DIR}/src/utils
)

# Light PCH + light ABI: AIAssistantPlugin chỉ dùng Qt, không parse hay link
# VTK/OpenCV/PCL (enforced by tests/CheckArchitectureBoundaries.cmake).
target_precompile_headers(AIAssistantPlugin REUSE_FROM SharedPCHLight)
target_link_libraries(AIAssistantPlugin PRIVATE SharedPCHLight)

target_link_libraries(AIAssistantPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        Qt6::Network
        AppCore
)

set_target_properties(AIAssistantPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
