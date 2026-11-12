#include "AIAssistantRibbonUI.h"

#include "IAppContext.h"

AIAssistantRibbonUI::AIAssistantRibbonUI(IAppContext* ctx, QWidget* parentPanel, QObject* parent)
    : AbstractRibbonPanel(ctx, parent)
{
    if (!beginPanel(parentPanel, "aiAssistantGroup")) return;

    m_btnToggleAssistant = addButton("💬", "#10b981", "#059669",
                                     ctx->translate("ai.open_assistant"), 100);
    m_btnRestartModel = addButton("🔄", "#3b82f6", "#2563eb",
                                  ctx->translate("ai.restart_model"));
    m_btnRestartRAG = addButton("📚", "#eab308", "#ca8a04",
                                ctx->translate("ai.restart_rag"));
    m_btnRestartAgent = addButton("🤖", "#8b5cf6", "#7c3aed",
                                  ctx->translate("ai.restart_agent"));
    m_btnRestartServer = addButton("🖥️", "#ef4444", "#b91c1c",
                                   ctx->translate("ai.restart_server"));

    finishPanel(ctx->translate("menu.ai_assistant"));
}
