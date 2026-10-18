#ifndef AIPROCESSORRIBBONUI_H
#define AIPROCESSORRIBBONUI_H

#include "AbstractRibbonPanel.h"

class QToolButton;

class AIProcessorRibbonUI : public AbstractRibbonPanel {
    Q_OBJECT
public:
    explicit AIProcessorRibbonUI(IAppContext* ctx, QWidget* panel, QObject* parent = nullptr);

    QToolButton* btnDet() const { return m_btnDet; }
    QToolButton* btnSeg() const { return m_btnSeg; }
    QToolButton* btnHide() const { return m_btnHide; }
    QToolButton* btnTrack() const { return m_btnTrack; }
    QToolButton* btnPauseTrack() const { return m_btnPauseTrack; }
    QToolButton* btnStopTrack() const { return m_btnStopTrack; }
    QToolButton* btnTrain() const { return m_btnTrain; }
    QToolButton* btnChart() const { return m_btnChart; }
    QGroupBox* groupAI() const { return m_groupAI; }
    QGroupBox* groupTrain() const { return m_groupTrain; }

private:
    QToolButton* m_btnDet = nullptr;
    QToolButton* m_btnSeg = nullptr;
    QToolButton* m_btnHide = nullptr;
    QToolButton* m_btnTrack = nullptr;
    QToolButton* m_btnPauseTrack = nullptr;
    QToolButton* m_btnStopTrack = nullptr;
    QToolButton* m_btnTrain = nullptr;
    QToolButton* m_btnChart = nullptr;
    QGroupBox* m_groupAI = nullptr;
    QGroupBox* m_groupTrain = nullptr;
};

#endif // AIPROCESSORRIBBONUI_H
