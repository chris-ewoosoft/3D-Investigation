#include "ReconstructionRibbonUI.h"
#include "IAppContext.h"
#include "IconFactory.h"
#include <QGroupBox>
#include <QToolButton>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>

ReconstructionRibbonUI::ReconstructionRibbonUI(IAppContext* ctx, QObject* parent)
    : QObject(parent), m_ctx(ctx) {
}

void ReconstructionRibbonUI::setupUI() {
    if (QWidget* panel = m_ctx->getTabPanel("tab.reconstruction")) {
        auto *layout = qobject_cast<QHBoxLayout*>(panel->layout());
        if (layout) {
            QGroupBox *groupData = new QGroupBox(panel);
            groupData->setObjectName("reconDataGroup");
            groupData->setTitle("");

            QVBoxLayout *vbox = new QVBoxLayout(groupData);
            vbox->setContentsMargins(4, 4, 4, 4);
            vbox->setSpacing(2);

            QHBoxLayout *gLayout = new QHBoxLayout();
            gLayout->setContentsMargins(0, 0, 0, 0);
            gLayout->setSpacing(5);

            m_btnLoad = new QToolButton(groupData);
            m_btnLoad->setIcon(IconFactory::createModern("📂", QColor("#3b82f6"), QColor("#2563eb")));
            m_btnLoad->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
            m_btnLoad->setMinimumWidth(80);

            m_btnRun = new QToolButton(groupData);
            m_btnRun->setIcon(IconFactory::createModern("⚡", QColor("#f59e0b"), QColor("#d97706")));
            m_btnRun->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
            m_btnRun->setMinimumWidth(80);

            m_btnToggleCloud = new QToolButton(groupData);
            m_btnToggleCloud->setIcon(IconFactory::createModern("🌐", QColor("#10b981"), QColor("#059669")));
            m_btnToggleCloud->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
            m_btnToggleCloud->setMinimumWidth(100);

            gLayout->addWidget(m_btnLoad);
            gLayout->addWidget(m_btnRun);
            gLayout->addWidget(m_btnToggleCloud);
            vbox->addLayout(gLayout);
            vbox->addStretch();

            QLabel *titleLabel = new QLabel(groupData);
            titleLabel->setObjectName("groupTitleLabel");
            titleLabel->setAlignment(Qt::AlignCenter);
            titleLabel->setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: bold;");
            vbox->addWidget(titleLabel);

            layout->insertWidget(layout->count() - 1, groupData);
        }
    }
    updateTranslations();
}

void ReconstructionRibbonUI::updateTranslations() {
    if (m_btnLoad) m_btnLoad->setText(m_ctx->translate("recon.load_images"));
    if (m_btnRun) m_btnRun->setText(m_ctx->translate("recon.start"));
    
    if (m_btnLoad && m_btnLoad->parentWidget()) {
        if (QGroupBox* gb = qobject_cast<QGroupBox*>(m_btnLoad->parentWidget())) {
            if (QLabel *lbl = gb->findChild<QLabel*>("groupTitleLabel")) {
                lbl->setText(m_ctx->translate("recon.menu"));
            }
        }
    }
}

void ReconstructionRibbonUI::updateStates(bool isPointCloudVisible) {
    if (m_btnToggleCloud) {
        m_btnToggleCloud->setText(isPointCloudVisible ? m_ctx->translate("common.close") + " " + m_ctx->translate("recon.view_model") : m_ctx->translate("recon.view_model"));
    }
}
