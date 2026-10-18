#ifndef AITRAINDOCKWIDGET_H
#define AITRAINDOCKWIDGET_H

#include <QDockWidget>
#include <QProcess>
#include <QTextBrowser>
#include <QProgressBar>
#include <QPushButton>

class IAppContext;
class QLabel;

class AITrainDockWidget : public QDockWidget {
    Q_OBJECT
public:
    explicit AITrainDockWidget(IAppContext* ctx, QWidget* parent = nullptr);
    ~AITrainDockWidget();

    void startTraining(bool autoConfirm = false);
    /** Re-apply translated texts (title, stop button) after a language change. */
    void retranslate();

private slots:
    void closeTrainDock();
    void onStopTrain();
    void onTrainProcessOutput();
    void onTrainProcessFinished(int exitCode, QProcess::ExitStatus exitStatus);

private:
    IAppContext* m_ctx;
    QProcess* trainProcess;
    QProgressBar* trainProgressBar;
    QTextBrowser* trainLogBrowser;
    QPushButton* btnStopTrain;
    QLabel* m_titleLabel = nullptr;
};

#endif // AITRAINDOCKWIDGET_H
