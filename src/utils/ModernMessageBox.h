#ifndef MODERN_MESSAGE_BOX_H
#define MODERN_MESSAGE_BOX_H

#include "ModernDialog.h"
#include "LanguageManager.h"
#include <QLabel>
#include <QPushButton>
#include <QHBoxLayout>
#include <QVBoxLayout>

class APP_EXPORT ModernMessageBox {
public:
    static void information(QWidget* parent, const QString& title, const QString& text) {
        showMessage(parent, title, text, "information");
    }
    
    static void warning(QWidget* parent, const QString& title, const QString& text) {
        showMessage(parent, title, text, "warning");
    }
    
    static void critical(QWidget* parent, const QString& title, const QString& text) {
        showMessage(parent, title, text, "critical");
    }
    
    static bool question(QWidget* parent, const QString& title, const QString& text) {
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
        
        QPushButton* yesBtn = new QPushButton(LM_TR("common.yes"), content);
        yesBtn->setObjectName("primary");
        QPushButton* noBtn = new QPushButton(LM_TR("common.no"), content);
        
        btnLayout->addWidget(yesBtn);
        btnLayout->addWidget(noBtn);
        layout->addLayout(btnLayout);
        
        dlg.setContentLayout(layout);
        
        QObject::connect(yesBtn, &QPushButton::clicked, &dlg, &QDialog::accept);
        QObject::connect(noBtn, &QPushButton::clicked, &dlg, &QDialog::reject);
        
        return dlg.exec() == QDialog::Accepted;
    }

private:
    static void showMessage(QWidget* parent, const QString& title, const QString& text, const QString& type) {
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
        if (type == "critical") {
            okBtn->setStyleSheet("background-color: #ef4444; color: white; border: none;");
        } else {
            okBtn->setObjectName("primary");
        }
        
        btnLayout->addWidget(okBtn);
        layout->addLayout(btnLayout);
        
        dlg.setContentLayout(layout);
        
        QObject::connect(okBtn, &QPushButton::clicked, &dlg, &QDialog::accept);
        
        dlg.exec();
    }
};

#endif // MODERN_MESSAGE_BOX_H
