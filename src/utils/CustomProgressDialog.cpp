#include "CustomProgressDialog.h"
#include "LanguageManager.h"
#include <QVBoxLayout>
#include <QHBoxLayout>

CustomProgressDialog::CustomProgressDialog(QWidget *parent) : QDialog(parent) {
    // Tắt viền cửa sổ chuẩn
    this->setWindowFlags(Qt::Dialog | Qt::FramelessWindowHint | Qt::WindowSystemMenuHint);
    this->setAttribute(Qt::WA_TranslucentBackground);
    this->setWindowModality(Qt::ApplicationModal);
    this->setStyleSheet("QDialog { background-color: transparent; } #progressContainer { background-color: #1a1a1f; border: 1px solid #3a3a4a; border-radius: 8px; }");
    this->setMinimumWidth(500);

    QVBoxLayout *baseLayout = new QVBoxLayout(this);
    baseLayout->setContentsMargins(10, 10, 10, 10);
    QWidget *container = new QWidget(this);
    container->setObjectName("progressContainer");
    baseLayout->addWidget(container);

    QVBoxLayout *mainLayout = new QVBoxLayout(container);
    mainLayout->setContentsMargins(20, 20, 20, 20);
    mainLayout->setSpacing(15);

    // Dòng 1: Title và Nút Stop
    QHBoxLayout *topLayout = new QHBoxLayout();
    
    lblTitle = new QLabel(LM_TR("common.loading"), this);
    lblTitle->setStyleSheet("color: #ffffff; font-size: 14px; font-weight: bold;");
    
    btnStop = new QPushButton(LM_TR("progress.stop"), this);
    btnStop->setCursor(Qt::PointingHandCursor);
    btnStop->setStyleSheet(
        "QPushButton { background:#d32f2f; color:#fff; border:none; border-radius:4px; padding:6px 16px; font-weight:bold; font-size: 13px; }"
        "QPushButton:hover { background:#b71c1c; }"
    );
    connect(btnStop, &QPushButton::clicked, this, &CustomProgressDialog::onStopClicked);
    connect(&LanguageManager::instance(), &LanguageManager::languageChanged, this, [this]() {
        btnStop->setText(LM_TR("progress.stop"));
    });

    topLayout->addWidget(lblTitle);
    topLayout->addStretch();
    topLayout->addWidget(btnStop);

    // Dòng 2: ProgressBar
    progressBar = new QProgressBar(this);
    progressBar->setFixedHeight(12);
    progressBar->setTextVisible(false); // Ẩn text bên trong bar cho gọn
    progressBar->setRange(0, 0); // Chạy liên tục mặc định
    progressBar->setStyleSheet(
        "QProgressBar { background:#2a2a35; border:none; border-radius:6px; }"
        "QProgressBar::chunk { background-color: #10a37f; border-radius:6px; }"
    );

    mainLayout->addLayout(topLayout);
    mainLayout->addWidget(progressBar);
}

void CustomProgressDialog::setRange(int minimum, int maximum) {
    progressBar->setRange(minimum, maximum);
}

void CustomProgressDialog::setValue(int value) {
    progressBar->setValue(value);
}

void CustomProgressDialog::setLabelText(const QString &text) {
    lblTitle->setText(text);
}

void CustomProgressDialog::reset() {
    progressBar->reset();
}

void CustomProgressDialog::centerOnWidget(QWidget* target) {
    if (!target) {
        target = parentWidget();
    }
    if (target) {
        QPoint center = target->mapToGlobal(target->rect().center());
        // Need to ensure the dialog size is correct before moving.
        // It's usually better to call this after show(), or adjust geometry directly.
        move(center.x() - width() / 2, center.y() - height() / 2);
    }
}

void CustomProgressDialog::onStopClicked() {
    emit stopRequested();
}
