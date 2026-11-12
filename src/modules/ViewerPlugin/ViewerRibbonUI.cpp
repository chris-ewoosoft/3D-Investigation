#include "ViewerRibbonUI.h"

#include "IAppContext.h"

ViewerRibbonUI::ViewerRibbonUI(IAppContext* ctx, QWidget* panel, QObject* parent)
    : AbstractRibbonPanel(ctx, parent)
{
    if (!beginPanel(panel, "viewerDataGroup")) return;

    m_btnLoad2D = addButton("🖼️", "#3b82f6", "#2563eb", ctx->translate("viewer.load_2d"));
    m_btnLoad3D = addButton("📦", "#8b5cf6", "#7c3aed", ctx->translate("viewer.load_3d"));
    m_btnLoadDicom = addButton("🏥", "#ec4899", "#db2777", ctx->translate("viewer.load_dicom"));

    finishPanel(ctx->translate("viewer.menu"));
}
