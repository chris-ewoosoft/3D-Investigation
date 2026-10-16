#include "SendMailRibbonUI.h"

#include "IAppContext.h"
#include "IconFactory.h"

#include <QGroupBox>
#include <QHBoxLayout>
#include <QColor>
#include <QIcon>
#include <QLabel>
#include <QToolButton>
#include <QVBoxLayout>

SendMailRibbonUI::SendMailRibbonUI(IAppContext *ctx, QWidget *panel, QObject *parent)
    : QObject(parent), m_ctx(ctx)
{
    auto *layout = qobject_cast<QHBoxLayout*>(panel->layout());
    if (!layout) return;

    m_groupMail = new QGroupBox(panel);
    m_groupMail->setObjectName("sendMailGroup");
    m_groupMail->setTitle("");

    auto *vbox = new QVBoxLayout(m_groupMail);
    vbox->setContentsMargins(4, 4, 4, 4);
    vbox->setSpacing(2);

    auto *row = new QHBoxLayout();
    row->setContentsMargins(0, 0, 0, 0);
    row->setSpacing(5);

    auto setupButton = [](QToolButton *button, const QIcon &icon, const QString &text) {
        button->setIcon(icon);
        button->setText(text);
        button->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
        button->setMinimumWidth(78);
    };

    m_btnToggleMail = new QToolButton(m_groupMail);
    setupButton(m_btnToggleMail,
                IconFactory::createModern("☒", QColor("#ef4444"), QColor("#b91c1c")),
                m_ctx->translate("mail.open_close"));
    m_btnToggleMail->setMinimumWidth(100);

    m_btnSettings = new QToolButton(m_groupMail);
    setupButton(m_btnSettings,
                IconFactory::createModern("⚙", QColor("#f59e0b"), QColor("#b45309")),
                m_ctx->translate("mail.settings"));

    row->addWidget(m_btnToggleMail);
    row->addWidget(m_btnSettings);
    vbox->addLayout(row);
    vbox->addStretch();

    auto *titleLabel = new QLabel(m_ctx->translate("mail.menu"), m_groupMail);
    titleLabel->setObjectName("groupTitleLabel");
    titleLabel->setAlignment(Qt::AlignCenter);
    titleLabel->setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: bold;");
    vbox->addWidget(titleLabel);

    layout->insertWidget(layout->count() - 1, m_groupMail);
}
