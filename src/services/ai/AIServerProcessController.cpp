#include "AIServerProcessController.h"

#include "AppConstants.h"
#include "IAppPaths.h"
#include "ILanguageService.h"

#include <QDebug>
#include <QFileInfo>
#include <QProcessEnvironment>
#include <QStandardPaths>

#include <utility>

AIServerProcessController::AIServerProcessController(ILanguageService *lang,
                                                     IAppPaths *paths,
                                                     QString pythonInterpreter,
                                                     QObject *parent)
    : QObject(parent)
    , m_lang(lang)
    , m_paths(paths)
    , m_pythonInterpreter(pythonInterpreter.isEmpty() ? QStringLiteral("python")
                                                      : std::move(pythonInterpreter))
    , m_process(new QProcess(this))
{
    // Bounded fallback for a wrapper that ignores terminate(): armed by
    // requestStop(), disarmed in onFinished().
    m_killTimer.setSingleShot(true);
    connect(&m_killTimer, &QTimer::timeout,
            this, &AIServerProcessController::onKillTimeout);
    connectProcess();
}

AIServerProcessController::~AIServerProcessController()
{
    // Shutdown path: the app is exiting, so a bounded blocking reap is
    // acceptable here (and required — the QProcess child must not outlive this
    // object). This is the ONLY place that waits on the process; the normal
    // stop() path is non-blocking (see requestStop()).
    m_killTimer.stop();
    disconnectProcess();
    if (m_process->state() != QProcess::NotRunning) {
        m_process->terminate();
        if (!m_process->waitForFinished(1000)) {
            m_process->kill();
            m_process->waitForFinished(AppConstants::AIServer::STOP_SERVER_TIMEOUT_MS);
        }
    }
}

void AIServerProcessController::connectProcess()
{
    connect(m_process, &QProcess::readyReadStandardOutput,
            this, &AIServerProcessController::onReadyRead);
    connect(m_process, &QProcess::readyReadStandardError,
            this, &AIServerProcessController::onErrorRead);
    connect(m_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
            this, &AIServerProcessController::onFinished);
}

void AIServerProcessController::disconnectProcess()
{
    disconnect(m_process, nullptr, this, nullptr);
}

void AIServerProcessController::start(int modelIndex)
{
    if (m_process->state() != QProcess::NotRunning) {
        // A start while the previous wrapper is still exiting must not call
        // QProcess::start() on a running process (it fails): remember the
        // request and launch it once the process has finished. This is what
        // lets stop() stay non-blocking without breaking stop()+start().
        m_pendingModelIndex = modelIndex;
        requestStop();
        return;
    }
    launch(modelIndex);
}

void AIServerProcessController::launch(int modelIndex)
{
    const QStringList models = AppConstants::AIAssistant::modelNames();
    const QString modelName =
        (modelIndex >= 0 && modelIndex < models.size()) ? models.at(modelIndex)
                                                        : QStringLiteral("Unknown Model");
    qDebug() << "[AI Server] Starting server with model index:" << modelIndex
             << "(" << modelName << ")";

    m_readyEmitted = false;
    m_stopping = false;
    m_pendingModelIndex = -1;
    disconnectProcess();
    connectProcess();

    QString program;
    QStringList args;
    if (!buildLaunchCommand(modelIndex, program, args))
        return;

    if (m_lang) emit serverStatusChanged(m_lang->translate("ai.starting_server"));

    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert("PYTHONUNBUFFERED", "1");
    env.insert("PYTHONIOENCODING", "utf-8");
    env.insert("APP_DATA_DIR", QStandardPaths::writableLocation(QStandardPaths::AppDataLocation));
    m_process->setProcessEnvironment(env);
    m_process->setWorkingDirectory(m_paths->aiAssistantDir());
    m_process->start(program, args);
}

bool AIServerProcessController::buildLaunchCommand(int modelIndex, QString &program,
                                                   QStringList &args)
{
    const QString script = (m_paths ? m_paths->aiAssistantDir() : QString())
                           + "/" + AppConstants::AIServer::chatbotScript();
    if (!QFileInfo::exists(script)) {
        if (m_lang) emit errorOccurred(m_lang->translate("ai.missing_script"));
        return false;
    }

    program = m_pythonInterpreter;
    args = QStringList() << "-u" << script << QString::number(modelIndex);
    return true;
}

void AIServerProcessController::stop()
{
    // Non-blocking. The previous implementation called waitForFinished(1000)
    // and then waitForFinished(STOP_SERVER_TIMEOUT_MS) on the GUI thread: on
    // Windows a console wrapper ignores WM_CLOSE, so terminate() always timed
    // out and the window froze ~1 s on every start/stop/restart. Now the event
    // loop reaps the process and a timer arms the bounded kill fallback.
    requestStop();
}

void AIServerProcessController::requestStop()
{
    if (m_process->state() == QProcess::NotRunning)
        return;

    m_stopping = true;
    // Keep the detached AI-server process running after Qt exits: terminate
    // the wrapper first, then kill it only when the bounded wait expires.
    m_process->terminate();
    m_killTimer.start(AppConstants::AIServer::STOP_SERVER_TIMEOUT_MS);
}

void AIServerProcessController::onKillTimeout()
{
    if (m_process->state() != QProcess::NotRunning)
        m_process->kill();
}

bool AIServerProcessController::isRunning() const
{
    return m_process && m_process->state() != QProcess::NotRunning;
}

void AIServerProcessController::detectReady(const QString &output)
{
    if (m_readyEmitted) return;
    if (output.contains("[SUCCESS] AI Server") ||
        output.contains("Uvicorn running on") ||
        output.contains("Application startup complete")) {
        m_readyEmitted = true;
        emit serverReady();
    }
}

void AIServerProcessController::onReadyRead()
{
    const QString output = QString::fromUtf8(m_process->readAllStandardOutput());
    if (!output.isEmpty())
        qDebug().noquote() << QString("[AIAssistant Server]\n%1").arg(output.trimmed());

    const QStringList lines = output.split('\n');
    for (const QString &line : lines) {
        const int index = line.indexOf("[STATUS]");
        if (index >= 0 && m_lang)
            emit serverStatusChanged(line.mid(index + 8).trimmed());
    }

    detectReady(output);
}

void AIServerProcessController::onErrorRead()
{
    const QString output = QString::fromUtf8(m_process->readAllStandardError());
    if (!output.isEmpty())
        qDebug().noquote() << QString("[AIAssistant Server]\n%1").arg(output.trimmed());

    const QStringList lines = output.split('\n');
    for (const QString &line : lines) {
        const int index = line.indexOf("[STATUS]");
        if (index >= 0 && m_lang)
            emit serverStatusChanged(line.mid(index + 8).trimmed());
    }

    detectReady(output);
}

void AIServerProcessController::onFinished(int exitCode, QProcess::ExitStatus status)
{
    m_readyEmitted = false;
    m_killTimer.stop();

    const bool wasStopping = m_stopping;
    m_stopping = false;

    // A stop()/restart() we asked for exits non-zero; that is expected and
    // must not be reported as a server crash. Only an exit we did not request
    // is an "unexpected finish" the owner may want to recover from.
    if (!wasStopping)
        emit processFinished(exitCode, status == QProcess::CrashExit);

    // A start() that arrived while the wrapper was exiting is launched now.
    if (m_pendingModelIndex >= 0) {
        const int index = m_pendingModelIndex;
        m_pendingModelIndex = -1;
        launch(index);
    }
}
