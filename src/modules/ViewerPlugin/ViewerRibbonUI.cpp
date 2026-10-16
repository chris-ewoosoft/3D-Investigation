#include "ViewerRibbonUI.h"
#include "IAppContext.h"
#include "IconFactory.h"
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGroupBox>
#include <QToolButton>
#include <QLabel>

ViewerRibbonUI::ViewerRibbonUI(IAppContext* ctx, QWidget* panel, QObject* parent)
    : QObject(parent), m_ctx(ctx)
{
    auto *layout = qobject_cast<QHBoxLayout*>(panel->layout());
    if (!layout) return;

    m_groupView = new QGroupBox(panel);
    m_groupView->setObjectName("viewerDataGroup");
    m_groupView->setTitle("");

    QVBoxLayout *vbox = new QVBoxLayout(m_groupView);
    vbox->setContentsMargins(4, 4, 4, 4);
    vbox->setSpacing(2);

    QHBoxLayout *gLayout = new QHBoxLayout();
    gLayout->setContentsMargins(0, 0, 0, 0);
    gLayout->setSpacing(5);

    m_btnLoad2D = new QToolButton(m_groupView);
    m_btnLoad2D->setText(m_ctx->translate("viewer.load_2d"));
    m_btnLoad2D->setIcon(IconFactory::createModern("🖼️", QColor("#3b82f6"), QColor("#2563eb")));
    m_btnLoad2D->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
    m_btnLoad2D->setMinimumWidth(80);

    m_btnLoad3D = new QToolButton(m_groupView);
    m_btnLoad3D->setText(m_ctx->translate("viewer.load_3d"));
    m_btnLoad3D->setIcon(IconFactory::createModern("📦", QColor("#8b5cf6"), QColor("#7c3aed")));
    m_btnLoad3D->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
    m_btnLoad3D->setMinimumWidth(80);

    m_btnLoadDicom = new QToolButton(m_groupView);
    m_btnLoadDicom->setText(m_ctx->translate("viewer.load_dicom"));
    m_btnLoadDicom->setIcon(IconFactory::createModern("🏥", QColor("#ec4899"), QColor("#db2777")));
    m_btnLoadDicom->setToolButtonStyle(Qt::ToolButtonTextUnderIcon);
    m_btnLoadDicom->setMinimumWidth(80);

    gLayout->addWidget(m_btnLoad2D);
    gLayout->addWidget(m_btnLoad3D);
    gLayout->addWidget(m_btnLoadDicom);
    vbox->addLayout(gLayout);
    vbox->addStretch();

    QLabel *titleLabel = new QLabel(m_ctx->translate("viewer.menu"), m_groupView);
    titleLabel->setObjectName("groupTitleLabel");
    titleLabel->setAlignment(Qt::AlignCenter);
    titleLabel->setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: bold;");
    vbox->addWidget(titleLabel);

    layout->insertWidget(layout->count() - 1, m_groupView);
}
