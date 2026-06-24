#ifndef AIPROCESSORRIBBONUI_H
#define AIPROCESSORRIBBONUI_H

#include <QObject>

class QWidget;
class QToolButton;
class IAppContext;
class QGroupBox;

class AIProcessorRibbonUI : public QObject {
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
    IAppContext* m_ctx;
    QToolButton* m_btnDet;
    QToolButton* m_btnSeg;
    QToolButton* m_btnHide;
    QToolButton* m_btnTrack;
    QToolButton* m_btnPauseTrack;
    QToolButton* m_btnStopTrack;
    QToolButton* m_btnTrain;
    QToolButton* m_btnChart;
    QGroupBox* m_groupAI;
    QGroupBox* m_groupTrain;
};

#endif // AIPROCESSORRIBBONUI_H
