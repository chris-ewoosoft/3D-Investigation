#ifndef RECONSTRUCTION_RIBBON_UI_H
#define RECONSTRUCTION_RIBBON_UI_H

#include <QObject>

class IAppContext;
class QToolButton;
class QAction;

class ReconstructionRibbonUI : public QObject {
    Q_OBJECT
public:
    explicit ReconstructionRibbonUI(IAppContext* ctx, QObject* parent = nullptr);
    ~ReconstructionRibbonUI() override = default;

    void setupUI();
    void updateTranslations();
    void updateStates(bool isPointCloudVisible);

    QToolButton* btnLoad() const { return m_btnLoad; }
    QToolButton* btnRun() const { return m_btnRun; }
    QToolButton* btnToggleCloud() const { return m_btnToggleCloud; }

private:
    IAppContext* m_ctx = nullptr;
    QToolButton* m_btnLoad = nullptr;
    QToolButton* m_btnRun = nullptr;
    QToolButton* m_btnToggleCloud = nullptr;
};

#endif // RECONSTRUCTION_RIBBON_UI_H
