#ifndef VIEWERRIBBONUI_H
#define VIEWERRIBBONUI_H

#include <QObject>

class QWidget;
class QToolButton;
class IAppContext;
class QGroupBox;

class ViewerRibbonUI : public QObject {
    Q_OBJECT
public:
    explicit ViewerRibbonUI(IAppContext* ctx, QWidget* panel, QObject* parent = nullptr);
    
    QToolButton* btnLoad2D() const { return m_btnLoad2D; }
    QToolButton* btnLoad3D() const { return m_btnLoad3D; }
    QToolButton* btnLoadDicom() const { return m_btnLoadDicom; }
    QGroupBox* groupView() const { return m_groupView; }

private:
    IAppContext* m_ctx;
    QToolButton* m_btnLoad2D;
    QToolButton* m_btnLoad3D;
    QToolButton* m_btnLoadDicom;
    QGroupBox* m_groupView;
};

#endif // VIEWERRIBBONUI_H
