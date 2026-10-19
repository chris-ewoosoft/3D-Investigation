#include "ReconstructionRibbonUI.h"

#include "AppConstants.h"
#include "IAppContext.h"
#include "IShellService.h"

#include <QGroupBox>
#include <QLabel>

ReconstructionRibbonUI::ReconstructionRibbonUI(IAppContext* ctx, QObject* parent)
    : AbstractRibbonPanel(ctx, parent) {
}

void ReconstructionRibbonUI::setupUI() {
    if (QWidget* panel = ctx()->shell()->getTabPanel(AppConstants::TabIds::Reconstruction)) {
        if (beginPanel(panel, "reconDataGroup")) {
            // Labels are filled by updateTranslations() so they follow the
            // active language immediately after construction.
            m_btnLoad = addButton("📂", "#3b82f6", "#2563eb", QString());
            m_btnRun = addButton("⚡", "#f59e0b", "#d97706", QString());
            m_btnToggleCloud = addButton("🌐", "#10b981", "#059669", QString(), 100);
            finishPanel(QString());
        }
    }
    updateTranslations();
}

void ReconstructionRibbonUI::updateTranslations() {
    if (m_btnLoad) m_btnLoad->setText(ctx()->translate("recon.load_images"));
    if (m_btnRun) m_btnRun->setText(ctx()->translate("recon.start"));

    if (m_btnLoad && m_btnLoad->parentWidget()) {
        if (QGroupBox* gb = qobject_cast<QGroupBox*>(m_btnLoad->parentWidget())) {
            if (QLabel *lbl = gb->findChild<QLabel*>("groupTitleLabel")) {
                lbl->setText(ctx()->translate("recon.menu"));
            }
        }
    }
}

void ReconstructionRibbonUI::updateStates(bool isPointCloudVisible) {
    if (m_btnToggleCloud) {
        m_btnToggleCloud->setText(isPointCloudVisible ? ctx()->translate("common.close") + " " + ctx()->translate("recon.view_model") : ctx()->translate("recon.view_model"));
    }
}
