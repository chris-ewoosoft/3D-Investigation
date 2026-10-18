#ifndef STYLE_MANAGER_H
#define STYLE_MANAGER_H

#include <QString>
#include "Global.h"
class QApplication;

class APP_EXPORT StyleManager {
public:
    static void applyTheme(QApplication *app);
    static void setAccentColor(const QString &colorHex);
    static QString accentColor() { return m_accentColor; }
    static QString getGlobalStyleSheet();
    static void setGlassmorphismEnabled(bool enabled);
    static bool isGlassmorphismEnabled() { return m_glassmorphism; }

private:
    static QString m_accentColor;
    static bool    m_glassmorphism;
};

#endif // STYLE_MANAGER_H
