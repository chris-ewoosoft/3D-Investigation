#include <QtTest>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QThread>

#include "AIServerProcessController.h"
#include "IAppPaths.h"
#include "ILanguageService.h"

#include <cstdio>
#include <memory>

/**
 * @brief Contract test for the non-blocking server-process teardown.
 *
 * The controller used to call waitForFinished() on the GUI thread in stop():
 * on Windows a console wrapper ignores WM_CLOSE, so terminate() always timed
 * out and the window froze ~1 s on every start/stop/restart. The fix makes
 * stop() non-blocking (terminate + a kill timer, reaped in onFinished) and
 * defers a start() that arrives while the wrapper is still exiting.
 *
 * The controller launches `python -u <script>` — this test overrides the
 * launch command (the protected seam) to run *this executable* as a
 * controllable stand-in for the Python wrapper.
 */

// ── Minimal fakes for the injected dependencies ─────────────────────────────

class FakeLanguageService : public ILanguageService {
    Q_OBJECT
public:
    FakeLanguageService() = default;
    QString currentLanguage() const override { return QStringLiteral("en"); }
    void setLanguage(const QString &) override {}
    QString translate(const QString &key) const override { return key; }
    QString translate(const char *key) const override { return QString::fromLatin1(key); }
    QString displayName() const override { return QStringLiteral("English"); }
    QString flagEmoji() const override { return QString(); }
};

class FakePaths : public IAppPaths {
public:
    explicit FakePaths(QString dir) : m_dir(std::move(dir)) {}
    QString appDir() const override { return m_dir; }
    QString projectRootDir() const override { return m_dir; }
    QString dataRootDir() const override { return m_dir; }
    QString configPath() const override { return m_dir + "/config.ini"; }
    QString logsDir() const override { return m_dir; }
    QString modelsDir() const override { return m_dir; }
    QString predictDir(const QString &) const override { return m_dir; }
    QString aiAssistantDir() const override { return m_dir; }
    QString aiComputerVisionDir() const override { return m_dir; }
    QString uploadDir() const override { return m_dir; }
    QString thumbnailsDir() const override { return m_dir; }
    QString pluginsDir() const override { return m_dir; }

private:
    QString m_dir;
};

/// Launches this executable in "--fake-server" mode instead of Python.
class FakeServerController : public AIServerProcessController {
public:
    FakeServerController(ILanguageService *lang, IAppPaths *paths)
        : AIServerProcessController(lang, paths, QStringLiteral("unused"), nullptr) {}

    int buildCount = 0;
    int lastModelIndex = -1;
    int lifetimeMs = 60000; ///< how long the fake wrapper stays alive

protected:
    bool buildLaunchCommand(int modelIndex, QString &program, QStringList &args) override
    {
        ++buildCount;
        lastModelIndex = modelIndex;
        program = QCoreApplication::applicationFilePath();
        args << QStringLiteral("--fake-server") << QString::number(lifetimeMs)
             << QStringLiteral("--announce");
        return true;
    }
};

// ── Test object ─────────────────────────────────────────────────────────────

class TestAIServerProcessController : public QObject {
    Q_OBJECT

private slots:
    void missingScriptIsReportedThroughErrorOccurred();
    void stopIsNonBlockingAndDoesNotReportAUserStopAsACrash();
    void startWhileRunningIsDeferredUntilTheProcessExits();
    void unexpectedExitIsReportedAsProcessFinished();
    void destructorReapsARunningProcess();
};

void TestAIServerProcessController::missingScriptIsReportedThroughErrorOccurred()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    FakeLanguageService lang;
    FakePaths paths(dir.path());

    // The base class uses the real launch command: the temp AIAssistant dir
    // has no chatbot script, so start() must report the wiring problem and not
    // try to run anything.
    AIServerProcessController controller(&lang, &paths, QStringLiteral("python"));
    QSignalSpy errors(&controller, &AIServerProcessController::errorOccurred);

    controller.start(0);

    QCOMPARE(errors.count(), 1);
    QVERIFY(!controller.isRunning());
}

void TestAIServerProcessController::stopIsNonBlockingAndDoesNotReportAUserStopAsACrash()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    FakeLanguageService lang;
    FakePaths paths(dir.path());
    FakeServerController controller(&lang, &paths);

    QSignalSpy ready(&controller, &AIServerProcessController::serverReady);
    QSignalSpy finished(&controller, &AIServerProcessController::processFinished);

    controller.start(0);
    QVERIFY(controller.isRunning());
    // The fake wrapper announces readiness on stdout (and a [STATUS] line on
    // stderr) — both process channels are exercised.
    QVERIFY(ready.wait(5000));

    QElapsedTimer timer;
    timer.start();
    controller.stop();
    const qint64 elapsed = timer.elapsed();

    QVERIFY2(elapsed < 500,
             qPrintable(QStringLiteral("stop() blocked %1 ms on the caller")
                            .arg(elapsed)));

    // A console wrapper ignores terminate(); the armed kill timer reaps it.
    QTRY_VERIFY_WITH_TIMEOUT(!controller.isRunning(), 10000);
    // A stop we asked for is not a crash: no processFinished.
    QCOMPARE(finished.count(), 0);
}

void TestAIServerProcessController::startWhileRunningIsDeferredUntilTheProcessExits()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    FakeLanguageService lang;
    FakePaths paths(dir.path());
    FakeServerController controller(&lang, &paths);

    controller.start(0);
    QVERIFY(controller.isRunning());
    QCOMPARE(controller.buildCount, 1);
    QCOMPARE(controller.lastModelIndex, 0);

    // A second start while the wrapper is still running must be deferred, not
    // dropped and not attempted on a running QProcess.
    controller.start(1);
    QCOMPARE(controller.buildCount, 1);

    // The old wrapper is killed (console app ignores terminate), then the
    // deferred start launches the new model.
    QTRY_VERIFY_WITH_TIMEOUT(controller.buildCount == 2, 10000);
    QCOMPARE(controller.lastModelIndex, 1);
    QVERIFY(controller.isRunning());

    controller.stop();
    QTRY_VERIFY_WITH_TIMEOUT(!controller.isRunning(), 10000);
}

void TestAIServerProcessController::unexpectedExitIsReportedAsProcessFinished()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    FakeLanguageService lang;
    FakePaths paths(dir.path());
    FakeServerController controller(&lang, &paths);

    QSignalSpy finished(&controller, &AIServerProcessController::processFinished);

    // The wrapper exits on its own after 200 ms — an exit we did NOT request.
    controller.lifetimeMs = 200;
    controller.start(0);

    QVERIFY(finished.wait(5000));
    QCOMPARE(finished.first().at(0).toInt(), 0);      // clean exit code
    QCOMPARE(finished.first().at(1).toBool(), false); // not a crash
    QVERIFY(!controller.isRunning());
}

void TestAIServerProcessController::destructorReapsARunningProcess()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    FakeLanguageService lang;
    FakePaths paths(dir.path());

    {
        auto controller = std::make_unique<FakeServerController>(&lang, &paths);
        controller->start(0);
        QVERIFY(controller->isRunning());
        // The destructor is the one place allowed to block: the QProcess child
        // must not outlive the controller.
        controller.reset();
    }
    QVERIFY(true); // reaching here without a hang/crash is the assertion
}

int main(int argc, char *argv[])
{
    // Fake-wrapper child mode: the controller launches this executable with
    // these arguments in place of the Python server. Stay alive (or exit on a
    // timer) so the parent can drive the process lifecycle.
    if (argc >= 2 && QByteArray(argv[1]) == "--fake-server") {
        QCoreApplication app(argc, argv);
        const unsigned long lifetimeMs =
            (argc >= 3) ? static_cast<unsigned long>(QByteArray(argv[2]).toLongLong())
                        : 60000UL;
        if (argc >= 4 && QByteArray(argv[3]) == "--announce") {
            std::fputs("[SUCCESS] AI Server\n", stdout);
            std::fputs("[STATUS] fake server warming up\n", stderr);
            std::fflush(stdout);
            std::fflush(stderr);
        }
        QThread::msleep(lifetimeMs);
        return 0;
    }

    QCoreApplication app(argc, argv);
    TestAIServerProcessController tc;
    return QTest::qExec(&tc, argc, argv);
}

#include "TestAIServerProcessController.moc"
