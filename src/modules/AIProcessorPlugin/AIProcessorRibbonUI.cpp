#include "AIProcessorRibbonUI.h"

#include "IAppContext.h"

AIProcessorRibbonUI::AIProcessorRibbonUI(IAppContext* ctx, QWidget* panel, QObject* parent)
    : AbstractRibbonPanel(ctx, parent)
{
    if (!beginPanel(panel, "aiProcessingGroup")) return;

    m_btnDet = addButton("🔍", "#f59e0b", "#d97706", ctx->translate("ai.run_detection"));
    m_btnSeg = addButton("🎯", "#10b981", "#059669", ctx->translate("ai.run_segmentation"));
    m_btnTrack = addButton("🎥", "#ef4444", "#b91c1c", ctx->translate("ai.video_tracking"));
    m_btnPauseTrack = addButton("⏸️", "#f59e0b", "#d97706", ctx->translate("ai.pause"), 80, false);
    m_btnStopTrack = addButton("⏹️", "#ef4444", "#b91c1c", ctx->translate("aiproc.stop"), 80, false);
    m_btnHide = addButton("👁️", "#6b7280", "#4b5563", ctx->translate("ai.hide_results"));
    finishPanel(ctx->translate("ai.menu"));
    m_groupAI = group();

    // Second group in the same tab (Training).
    if (beginPanel(panel, "aiTrainingGroup")) {
        m_btnTrain = addButton("⚙️", "#8b5cf6", "#7c3aed", ctx->translate("ai.training"));
        m_btnChart = addButton("📊", "#06b6d4", "#0891b2", ctx->translate("ai.charts"));
        finishPanel(ctx->translate("ai.training"));
        m_groupTrain = group();
    }
}
