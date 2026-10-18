

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
    ${VTK_INCLUDE_DIRS}
    ${OpenCV_INCLUDE_DIRS}
)

target_precompile_headers(AIAssistantPlugin REUSE_FROM SharedPCH)
target_link_libraries(AIAssistantPlugin PRIVATE SharedPCH)

target_link_libraries(AIAssistantPlugin
    PRIVATE
        Qt6::Core
        Qt6::Widgets
        Qt6::Network
        ${VTK_LIBRARIES}
        AppCore
)

set_target_properties(AIAssistantPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins"
)
