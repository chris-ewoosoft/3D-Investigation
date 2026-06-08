#ifndef AI_ASSISTANT_RIBBON_UI_H
#define AI_ASSISTANT_RIBBON_UI_H

#include <QObject>
#include <QGroupBox>
#include <QToolButton>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include "IAppContext.h"

class AIAssistantRibbonUI : public QObject {
    Q_OBJECT
public:
    explicit AIAssistantRibbonUI(IAppContext* ctx, QWidget* parentPanel, QObject* parent = nullptr);

    QToolButton* btnToggleAssistant() const { return m_btnToggleAssistant; }
    QGroupBox* groupAI() const { return m_groupAI; }

private:


    IAppContext* m_ctx;
    QGroupBox* m_groupAI = nullptr;
    QToolButton* m_btnToggleAssistant = nullptr;
};

#endif // AI_ASSISTANT_RIBBON_UI_H
