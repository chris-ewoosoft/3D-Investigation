#ifndef THEME_SELECTION_DIALOG_H
#define THEME_SELECTION_DIALOG_H

#include "../../utils/ModernDialog.h"
#include <QGridLayout>
#include <QToolButton>
#include <QLineEdit>
#include <QSlider>
#include <QLabel>
#include <QCheckBox>

class QMainWindow;

class ThemeSelectionDialog : public QWidget {
    Q_OBJECT
public:
    /** username — để save/load accent color per-user */
    explicit ThemeSelectionDialog(const QString &username, QWidget *parent = nullptr);

    static void applyGlobalBackground(QMainWindow *mw, const QString &bgPath, int bgOpacity, bool useBg);

private:
    void addColorOption(QGridLayout *layout, int row, int col, const QString &name, const QString &color);
    void applyBackground();

    QString     m_selectedColor;
    QString     m_username;
    QString     m_bgImagePath;
    bool        m_useBgImage = false;
    int         m_bgOpacity = 30;   // 0-100

    QCheckBox  *m_useBgCheckbox = nullptr;
    QLineEdit  *m_bgPathEdit   = nullptr;
    QSlider    *m_opacitySlider = nullptr;
    QLabel     *m_opacityLabel  = nullptr;
    QLabel     *m_bgPreview     = nullptr;
};

#endif // THEME_SELECTION_DIALOG_H
