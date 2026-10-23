#ifndef MODERN_MESSAGE_BOX_H
#define MODERN_MESSAGE_BOX_H

#include "ModernDialog.h"
#include "ILanguageService.h"
#include "Global.h"
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

    static bool question(ILanguageService* lang, QWidget* parent, const QString& title, const QString& text);

private:
    // Definitions live in ModernMessageBox.cpp (AppCore) so plugins get exactly
    // one DLL-exported symbol per method instead of duplicate COMDAT copies.
    static void showMessage(QWidget* parent, const QString& title, const QString& text, const QString& type);
};

#endif // MODERN_MESSAGE_BOX_H
