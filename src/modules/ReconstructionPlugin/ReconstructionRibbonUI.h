#ifndef RECONSTRUCTION_RIBBON_UI_H
#define RECONSTRUCTION_RIBBON_UI_H

#include "AbstractRibbonPanel.h"

class QToolButton;

class ReconstructionRibbonUI : public AbstractRibbonPanel {
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
    QToolButton* m_btnLoad = nullptr;
    QToolButton* m_btnRun = nullptr;
    QToolButton* m_btnToggleCloud = nullptr;
};

#endif // RECONSTRUCTION_RIBBON_UI_H
