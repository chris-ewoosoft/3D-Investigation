#ifndef AI_ASSISTANT_RIBBON_UI_H
#define AI_ASSISTANT_RIBBON_UI_H

#include "AbstractRibbonPanel.h"

class QToolButton;

class AIAssistantRibbonUI : public AbstractRibbonPanel {
    Q_OBJECT
public:
    explicit AIAssistantRibbonUI(IAppContext* ctx, QWidget* parentPanel, QObject* parent = nullptr);

    QToolButton* btnToggleAssistant() const { return m_btnToggleAssistant; }
    QToolButton* btnRestartModel() const { return m_btnRestartModel; }
    QToolButton* btnRestartRAG() const { return m_btnRestartRAG; }
    QToolButton* btnRestartAgent() const { return m_btnRestartAgent; }
    QToolButton* btnRestartServer() const { return m_btnRestartServer; }
    QGroupBox* groupAI() const { return group(); }

private:
    QToolButton* m_btnToggleAssistant = nullptr;
    QToolButton* m_btnRestartModel = nullptr;
    QToolButton* m_btnRestartRAG = nullptr;
    QToolButton* m_btnRestartAgent = nullptr;
    QToolButton* m_btnRestartServer = nullptr;
};

#endif // AI_ASSISTANT_RIBBON_UI_H
