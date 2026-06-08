#include "AIAssistantRibbonUI.h"
#include "IconFactory.h"

AIAssistantRibbonUI::AIAssistantRibbonUI(IAppContext* ctx, QWidget* parentPanel, QObject* parent)
    : QObject(parent), m_ctx(ctx) 
{
    auto *layout = qobject_cast<QHBoxLayout*>(parentPanel->layout());
    if (layout) {
        m_groupAI = new QGroupBox(parentPanel);
        m_groupAI->setObjectName("aiAssistantGroup");
        m_groupAI->setTitle("");

        QVBoxLayout *vbox = new QVBoxLayout(m_groupAI);
        vbox->setContentsMargins(4, 4, 4, 4);
        vbox->setSpacing(2);

        QHBoxLayout *gLayout = new QHBoxLayout();
        gLayout->setContentsMargins(0, 0, 0, 0);
        gLayout->setSpacing(5);

        m_btnToggleAssistant = new QToolButton(m_groupAI);
        m_btnToggleAssistant->setText(m_ctx->translate("ai.open_assistant"));
        m_btnToggleAssistant->setIcon(IconFactory::createModern("💬", QColor("#10b981"), QColor("#059669")));
        m_btnToggleAssistant->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
        m_btnToggleAssistant->setMinimumWidth(100);

        gLayout->addWidget(m_btnToggleAssistant);
        vbox->addLayout(gLayout);
        vbox->addStretch();

        QLabel *titleLabel = new QLabel(m_ctx->translate("menu.ai_assistant"), m_groupAI);
        titleLabel->setObjectName("groupTitleLabel");
        titleLabel->setAlignment(Qt::AlignCenter);
        titleLabel->setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: bold;");
        vbox->addWidget(titleLabel);

        layout->insertWidget(layout->count() - 1, m_groupAI);
    }
}

