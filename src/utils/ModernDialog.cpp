#include "ModernDialog.h"
#include <QApplication>
#include <QScreen>

ModernDialog::ModernDialog(const QString &title, QWidget *parent)
    : QDialog(parent)
{
    setWindowFlags(Qt::Dialog | Qt::FramelessWindowHint);
    setAttribute(Qt::WA_TranslucentBackground);

    m_mainLayout = new QVBoxLayout(this);
    m_mainLayout->setContentsMargins(10, 10, 10, 10);
    m_mainLayout->setSpacing(0);

    QWidget *container = new QWidget(this);
    container->setObjectName("dialogContainer");
    container->setStyleSheet("#dialogContainer { border-radius: 12px; }");
    
    QVBoxLayout *containerLayout = new QVBoxLayout(container);
    containerLayout->setContentsMargins(0, 0, 0, 0);
    containerLayout->setSpacing(0);

    // Title Bar
    m_titleBar = new QWidget(container);
    m_titleBar->setObjectName("modernTitleBar");
    m_titleBar->setFixedHeight(45); // Increased height
    m_titleBar->setStyleSheet("#modernTitleBar { border-top-left-radius: 12px; border-top-right-radius: 12px; }");
    
    QHBoxLayout *titleLayout = new QHBoxLayout(m_titleBar);
    titleLayout->setContentsMargins(15, 0, 8, 0);
    
    m_titleLabel = new QLabel(title, m_titleBar);
    m_titleLabel->setObjectName("modernTitleLabel");
    
    m_closeBtn = new QPushButton("✕", m_titleBar);
    m_closeBtn->setObjectName("modernCloseBtn");
    m_closeBtn->setFixedSize(32, 32);
    m_closeBtn->setCursor(Qt::PointingHandCursor);
    
    titleLayout->addWidget(m_titleLabel);
    titleLayout->addStretch();
    titleLayout->addWidget(m_closeBtn);
    
    containerLayout->addWidget(m_titleBar);

    // Content Area
    m_contentArea = new QWidget(container);
    containerLayout->addWidget(m_contentArea);

    m_mainLayout->addWidget(container);

    connect(m_closeBtn, &QPushButton::clicked, this, &QDialog::reject);
}

void ModernDialog::setContentLayout(QLayout *layout) {
    if (m_contentArea->layout()) {
        delete m_contentArea->layout();
    }
    m_contentArea->setLayout(layout);
}

void ModernDialog::setWindowTitle(const QString &title) {
    m_titleLabel->setText(title);
}

void ModernDialog::mousePressEvent(QMouseEvent *event) {
    if (event->button() == Qt::LeftButton && m_titleBar->rect().contains(event->pos() - QPoint(10, 10))) {
        m_dragging = true;
        m_dragPos = event->globalPosition().toPoint() - frameGeometry().topLeft();
        event->accept();
    }
}

void ModernDialog::mouseMoveEvent(QMouseEvent *event) {
    if (m_dragging && (event->buttons() & Qt::LeftButton)) {
        move(event->globalPosition().toPoint() - m_dragPos);
        event->accept();
    }
}

void ModernDialog::mouseReleaseEvent(QMouseEvent *event) {
    m_dragging = false;
    event->accept();
}

void ModernDialog::showEvent(QShowEvent *event) {
    QDialog::showEvent(event);
    
    QWidget *pw = parentWidget();
    if (pw) {
        QPoint globalPos = pw->mapToGlobal(QPoint(0, 0));
        int x = globalPos.x() + (pw->width() - width()) / 2;
        int y = globalPos.y() + (pw->height() - height()) / 2;
        move(x, y);
    } else {
        QScreen *screen = QGuiApplication::primaryScreen();
        if (screen) {
            QRect screenGeometry = screen->geometry();
            int x = screenGeometry.left() + (screenGeometry.width() - width()) / 2;
            int y = screenGeometry.top() + (screenGeometry.height() - height()) / 2;
            move(x, y);
        }
    }
}
