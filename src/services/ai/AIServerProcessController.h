#ifndef AI_SERVER_PROCESS_CONTROLLER_H
#define AI_SERVER_PROCESS_CONTROLLER_H

#include <QObject>
#include <QProcess>
#include <QString>
#include <QTimer>

#include "Global.h"

class IAppPaths;
class ILanguageService;

/**
 * @brief Owns the Python AI-server process and its readiness handshake.
 *
 * AIAssistant keeps the request/session state machine; this controller owns
 * only process lifecycle (start/stop/terminate), environment setup and the
 * "[SUCCESS] AI Server" / "Uvicorn running" readiness detection. Keeping the
 * QProcess out of the orchestrator makes restart/shutdown paths testable and
 * prevents blocking waits from leaking into request code.
 */
class APP_EXPORT AIServerProcessController : public QObject {
    Q_OBJECT
public:
    AIServerProcessController(ILanguageService *lang, IAppPaths *paths,
                              QString pythonInterpreter, QObject *parent = nullptr);
    ~AIServerProcessController() override;

    void start(int modelIndex);
    /// Non-blocking: asks the wrapper to terminate and arms a bounded kill
    /// fallback; the process is reaped by the event loop (onFinished). A
    /// start() that arrives while the wrapper is still exiting is deferred,
    /// so stop()+start() callers keep working without the GUI thread waiting.
    void stop();
    bool isRunning() const;

signals:
    void serverStatusChanged(const QString &status);
    void errorOccurred(const QString &message);
    void serverReady();
    void processFinished(int exitCode, bool crashed);

protected:
    /// Test seam: build the program + args that launch the wrapper. The
    /// default runs `python -u <chatbot script> <modelIndex>` and reports a
    /// missing script through errorOccurred(); a test overrides it to run a
    /// controllable process instead of the real Python server.
    virtual bool buildLaunchCommand(int modelIndex, QString &program, QStringList &args);

private slots:
    void onReadyRead();
    void onErrorRead();
    void onFinished(int exitCode, QProcess::ExitStatus status);
    void onKillTimeout();

private:
    void connectProcess();
    void disconnectProcess();
    void detectReady(const QString &output);
    void launch(int modelIndex);
    void requestStop();

    ILanguageService *m_lang = nullptr;
    IAppPaths *m_paths = nullptr;
    QString m_pythonInterpreter;
    QProcess *m_process = nullptr;
    QTimer m_killTimer;
    bool m_readyEmitted = false;
    /// True between a stop()/restart() request and the process actually
    /// exiting: the (expected) non-zero exit must not be reported as a crash.
    bool m_stopping = false;
    /// Model index to launch once the current process has exited (deferred
    /// start), or -1 when there is nothing pending.
    int m_pendingModelIndex = -1;
};

#endif // AI_SERVER_PROCESS_CONTROLLER_H
