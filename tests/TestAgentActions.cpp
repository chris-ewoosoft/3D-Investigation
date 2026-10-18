#include <QTest>
#include <QSignalSpy>
#include <QVariantMap>
#include <QDir>
#include <QFile>
#include <QTemporaryDir>
#include "SignalBus.h"
#include "IAppPaths.h"
#include "AgentActionManifest.h"

namespace {
/// Minimal IAppPaths rooted at a temporary fixture directory.
class FakeAppPaths : public IAppPaths {
public:
    explicit FakeAppPaths(QString root) : m_root(std::move(root)) {}
    QString appDir() const override { return m_root; }
    QString projectRootDir() const override { return m_root; }
    QString dataRootDir() const override { return m_root; }
    QString configPath() const override { return m_root + "/Config/settings.ini"; }
    QString logsDir() const override { return m_root + "/logs"; }
    QString modelsDir() const override { return m_root + "/Models"; }
    QString predictDir(const QString &type) const override { return m_root + "/predict/" + type; }
    QString aiAssistantDir() const override { return m_root + "/AIAssistant"; }
    QString aiComputerVisionDir() const override { return m_root + "/AIComputerVision"; }
    QString uploadDir() const override { return m_root + "/uploads"; }
    QString thumbnailsDir() const override { return m_root + "/thumbnails"; }
    QString pluginsDir() const override { return m_root + "/plugins"; }

private:
    QString m_root;
};
} // namespace

class TestAgentActions : public QObject {
    Q_OBJECT

private slots:
    void testAuthActions() {
        SignalBus bus;
        QSignalSpy spy(&bus, &SignalBus::agentUiActionRequested);

        QVariantMap params;
        params["username"] = "Admin";
        params["password"] = "1";
        emit bus.agentUiActionRequested("admin.login", params);

        QCOMPARE(spy.count(), 1);
        QList<QVariant> args = spy.takeFirst();
        QCOMPARE(args.at(0).toString(), QString("admin.login"));
        QCOMPARE(args.at(1).toMap()["username"].toString(), QString("Admin"));
        QCOMPARE(args.at(1).toMap()["password"].toString(), QString("1"));

        emit bus.agentUiActionRequested("admin.logout", QVariantMap());
        QCOMPARE(spy.count(), 1);
        QCOMPARE(spy.takeFirst().at(0).toString(), QString("admin.logout"));
    }

    void testAssistantActions() {
        SignalBus bus;
        QSignalSpy spy(&bus, &SignalBus::agentUiActionRequested);
        
        QStringList actions = {
            "assistant.open", "assistant.close",
            "assistant.reload_model", "assistant.reload_rag",
            "assistant.reload_agent", "assistant.reload_server"
        };
        
        for (const QString& action : actions) {
            emit bus.agentUiActionRequested(action, QVariantMap());
            QCOMPARE(spy.count(), 1);
            QCOMPARE(spy.takeFirst().at(0).toString(), action);
        }
    }

    void testMailActions() {
        SignalBus bus;
        QSignalSpy spy(&bus, &SignalBus::agentUiActionRequested);
        
        QStringList actions = { "mail.open", "mail.close", "mail.settings" };
        for (const QString& action : actions) {
            emit bus.agentUiActionRequested(action, QVariantMap());
            QCOMPARE(spy.count(), 1);
            QCOMPARE(spy.takeFirst().at(0).toString(), action);
        }
    }
    
    void testAIActions() {
        SignalBus bus;
        QSignalSpy spy(&bus, &SignalBus::agentUiActionRequested);
        
        QStringList actions = {
            "ai.run_detection", "ai.run_segmentation", "ai.video_tracking",
            "ai.hide_results", "ai.training_model", "ai.view_training_charts"
        };
        
        for (const QString& action : actions) {
            emit bus.agentUiActionRequested(action, QVariantMap());
            QCOMPARE(spy.count(), 1);
            QCOMPARE(spy.takeFirst().at(0).toString(), action);
        }
    }

    void testReconstructionActions() {
        SignalBus bus;
        QSignalSpy spy(&bus, &SignalBus::agentUiActionRequested);
        
        QStringList actions = {
            "reconstruction.load_images", "reconstruction.start_reconstruction",
            "reconstruction.view_3d_model", "reconstruction.close_3d_model"
        };
        
        for (const QString& action : actions) {
            emit bus.agentUiActionRequested(action, QVariantMap());
            QCOMPARE(spy.count(), 1);
            QCOMPARE(spy.takeFirst().at(0).toString(), action);
        }
    }

    void testViewerActions() {
        SignalBus bus;
        QSignalSpy spy(&bus, &SignalBus::agentUiActionRequested);
        
        QStringList actions = {
            "viewer.load_2d", "viewer.load_3d", "viewer.load_dicom"
        };
        
        for (const QString& action : actions) {
            emit bus.agentUiActionRequested(action, QVariantMap());
            QCOMPARE(spy.count(), 1);
            QCOMPARE(spy.takeFirst().at(0).toString(), action);
        }
    }
    
    void testGeneralActions() {
        SignalBus bus;
        QSignalSpy spy(&bus, &SignalBus::agentUiActionRequested);
        
        QVariantMap langParams;
        langParams["language"] = "vi";
        emit bus.agentUiActionRequested("language.change", langParams);
        QCOMPARE(spy.count(), 1);
        QList<QVariant> args = spy.takeFirst();
        QCOMPARE(args.at(0).toString(), QString("language.change"));
        QCOMPARE(args.at(1).toMap()["language"].toString(), QString("vi"));
        
        emit bus.agentUiActionRequested("help.about", QVariantMap());
        QCOMPARE(spy.count(), 1);
        QCOMPARE(spy.takeFirst().at(0).toString(), QString("help.about"));
    }

    void testActionAcknowledgement() {
        SignalBus bus;
        QSignalSpy spy(&bus, &SignalBus::agentUiActionCompleted);

        QVariantMap result;
        result["action"] = "mail.open";
        emit bus.agentUiActionCompleted("request-42", true, result);

        QCOMPARE(spy.count(), 1);
        const QList<QVariant> args = spy.takeFirst();
        QCOMPARE(args.at(0).toString(), QString("request-42"));
        QCOMPARE(args.at(1).toBool(), true);
        QCOMPARE(args.at(2).toMap().value("action").toString(), QString("mail.open"));
    }

    /// The demo fixtures for agent actions must come from
    /// Config/agent_action_manifest.json ("sample_data"), never from paths
    /// hard-coded inside the plugins.
    void testSampleDefaultsComeFromManifest() {
        QTemporaryDir tmp;
        QVERIFY(tmp.isValid());
        const QDir root(tmp.path());
        const QDir makeDir(root.path());
        QVERIFY(makeDir.mkpath("Config"));
        QVERIFY(makeDir.mkpath("demo/images"));
        QVERIFY(makeDir.mkpath("demo/videos"));

        // Two images so the "first by name" rule is observable.
        for (const QString &name : {QStringLiteral("b.png"), QStringLiteral("a.jpg")}) {
            QFile f(root.filePath("demo/images/" + name));
            QVERIFY(f.open(QIODevice::WriteOnly));
            f.write("x");
        }
        QFile v(root.filePath("demo/videos/clip.mp4"));
        QVERIFY(v.open(QIODevice::WriteOnly));
        QFile model(root.filePath("demo/model.obj"));
        QVERIFY(model.open(QIODevice::WriteOnly));

        QFile mf(root.filePath("Config/agent_action_manifest.json"));
        QVERIFY(mf.open(QIODevice::WriteOnly));
        mf.write(R"({
  "actions": [],
  "sample_data": {
    "images_dir": "demo/images",
    "model_obj": "demo/model.obj",
    "dicom_dir": "demo/dicom",
    "video_dir": "demo/videos"
  }
})");
        mf.close();

        FakeAppPaths paths(root.path());
        const QVariantMap samples = AgentActionManifest::sampleData(&paths);
        QCOMPARE(samples.value("images_dir").toString(), QString("demo/images"));

        QVariantMap p2d;
        AgentActionManifest::applySampleDefaults("viewer.load_2d", p2d, &paths);
        QCOMPARE(p2d.value("path").toString(), root.filePath("demo/images/a.jpg"));

        QVariantMap p3d;
        AgentActionManifest::applySampleDefaults("viewer.load_3d", p3d, &paths);
        QCOMPARE(p3d.value("path").toString(), root.filePath("demo/model.obj"));

        // The DICOM fixture directory does not exist → no parameter invented.
        QVariantMap pd;
        AgentActionManifest::applySampleDefaults("viewer.load_dicom", pd, &paths);
        QVERIFY(!pd.contains("path"));

        QVariantMap pv;
        AgentActionManifest::applySampleDefaults("ai.video_tracking", pv, &paths);
        QCOMPARE(pv.value("path").toString(), root.filePath("demo/videos/clip.mp4"));

        QVariantMap pr;
        AgentActionManifest::applySampleDefaults("reconstruction.load_images", pr, &paths);
        QCOMPARE(pr.value("folder").toString(), root.filePath("demo/images"));

        // Caller-provided inputs always win over the fixture.
        QVariantMap explicitPath;
        explicitPath["path"] = "C:/custom.png";
        AgentActionManifest::applySampleDefaults("viewer.load_2d", explicitPath, &paths);
        QCOMPARE(explicitPath.value("path").toString(), QString("C:/custom.png"));
    }
};

QTEST_MAIN(TestAgentActions)
#include "TestAgentActions.moc"
