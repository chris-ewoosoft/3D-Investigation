#include "ReconstructionListUI.h"
#include "IAppContext.h"
#include <QMainWindow>
#include <QVBoxLayout>
#include <QListWidget>

ReconstructionListUI::ReconstructionListUI(IAppContext* ctx, QObject* parent)
    : QObject(parent), m_ctx(ctx) {
}

void ReconstructionListUI::setupUI() {
    if (QWidget* central = m_ctx->mainWindow()->centralWidget()) {
        if (QVBoxLayout* cLayout = qobject_cast<QVBoxLayout*>(central->layout())) {
            m_imageList = new QListWidget(central);
            m_imageList->setObjectName("reconstructionImageList");
            m_imageList->setFixedHeight(120);
            m_imageList->setViewMode(QListView::IconMode);
            m_imageList->setFlow(QListView::LeftToRight);
            m_imageList->setWrapping(false);
            m_imageList->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
            m_imageList->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
            m_imageList->setIconSize(QSize(80, 80));
            m_imageList->setResizeMode(QListView::Adjust);
            m_imageList->setSpacing(5);
            m_imageList->hide(); // Hidden until images are loaded
            cLayout->addWidget(m_imageList);
            
            connect(m_imageList, &QListWidget::currentRowChanged, this, &ReconstructionListUI::onListRowChanged);
        }
    }
}

void ReconstructionListUI::onListRowChanged(int row) {
    emit imageRowChanged(row);
}
