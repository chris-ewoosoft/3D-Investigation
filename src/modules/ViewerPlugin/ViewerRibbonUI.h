#ifndef VIEWERRIBBONUI_H
#define VIEWERRIBBONUI_H

#include "AbstractRibbonPanel.h"

class QToolButton;

class ViewerRibbonUI : public AbstractRibbonPanel {
    Q_OBJECT
public:
    explicit ViewerRibbonUI(IAppContext* ctx, QWidget* panel, QObject* parent = nullptr);

    QToolButton* btnLoad2D() const { return m_btnLoad2D; }
    QToolButton* btnLoad3D() const { return m_btnLoad3D; }
    QToolButton* btnLoadDicom() const { return m_btnLoadDicom; }
    QGroupBox* groupView() const { return group(); }

private:
    QToolButton* m_btnLoad2D = nullptr;
    QToolButton* m_btnLoad3D = nullptr;
    QToolButton* m_btnLoadDicom = nullptr;
};

#endif // VIEWERRIBBONUI_H
