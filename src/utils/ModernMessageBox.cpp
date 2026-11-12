#include "ModernMessageBox.h"

bool ModernMessageBox::question(ILanguageService* lang, QWidget* parent, const QString& title, const QString& text) {
    ModernDialog dlg(title, parent);
    dlg.setFixedSize(400, 200);
    QWidget* content = new QWidget(&dlg);
    QVBoxLayout* layout = new QVBoxLayout(content);
    layout->setContentsMargins(20, 20, 20, 20);
    QLabel* label = new QLabel(text, content);
    label->setWordWrap(true);
    label->setStyleSheet("color: #f1f5f9; font-size: 14px; background: transparent; border: none;");
    layout->addWidget(label);
    QHBoxLayout* btnLayout = new QHBoxLayout();
    btnLayout->addStretch();
    QPushButton* yesBtn = new QPushButton(lang ? lang->translate("common.yes") : QString("Yes"), content);
    yesBtn->setObjectName("primary");
    QPushButton* noBtn = new QPushButton(lang ? lang->translate("common.no") : QString("No"), content);
    btnLayout->addWidget(yesBtn);
    btnLayout->addWidget(noBtn);
    layout->addLayout(btnLayout);
    dlg.setContentLayout(layout);
    QObject::connect(yesBtn, &QPushButton::clicked, &dlg, &QDialog::accept);
    QObject::connect(noBtn, &QPushButton::clicked, &dlg, &QDialog::reject);
    return dlg.exec() == QDialog::Accepted;
}

void ModernMessageBox::showMessage(QWidget* parent, const QString& title, const QString& text, const QString& type) {
    ModernDialog dlg(title, parent);
    dlg.setFixedSize(400, 200);

    QWidget* content = new QWidget(&dlg);
    QVBoxLayout* layout = new QVBoxLayout(content);
    layout->setContentsMargins(20, 20, 20, 20);

    QLabel* label = new QLabel(text, content);
    label->setWordWrap(true);
    label->setStyleSheet("color: #f1f5f9; font-size: 14px; background: transparent; border: none;");

    layout->addWidget(label);

    QHBoxLayout* btnLayout = new QHBoxLayout();
    btnLayout->addStretch();
    QPushButton* okBtn = new QPushButton("OK", content);
    okBtn->setFixedSize(80, 35);
    if (type == "critical") {
        okBtn->setStyleSheet("background-color: #ef444d; color: white; border: none; font-size: 13px;}");
        okBtn->setObjectName("primary");
    } else {
        okBtn->setStyleSheet("background-color: #3b82f6; color: white; border: none; font-size: 13px;");
        okBtn->setObjectName("primary");
    }

    btnLayout->addWidget(okBtn);
    layout->addLayout(btnLayout);
    dlg.setContentLayout(layout);
    QObject::connect(okBtn, &QPushButton::clicked, &dlg, &QDialog::accept);
    dlg.exec();
}