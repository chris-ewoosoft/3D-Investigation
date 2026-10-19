#include <QtTest>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTimer>

#include <cstring>
#include <memory>
#include <utility>

#include "AIAssistant.h"
#include "AppConstants.h"
#include "IAppPaths.h"
#include "IAIAssistantService.h"
#include "ILanguageService.h"

/**
 * @brief AIAssistant request state machine against an injected transport.
 *
 * The production process/network dependencies are replaced by:
 *  - markServerReady(): the protected transition the QProcess stdout handler
 *    normally triggers, so queue draining is deterministic (no Python child);
 *  - FakeNetworkManager: records requests/bodies and completes replies with a
 *    canned JSON body, so no socket is opened.
 *
 * This covers the seams introduced for the AI service (injected interpreter +
 * port, injectable QNetworkAccessManager) plus session persistence.
 */

class FakeLanguageService : public ILanguageService {
public:
    QString currentLanguage() const override { return QStringLiteral("en"); }
    void setLanguage(const QString&) override {}
    QString translate(const QString& key) const override
    {
        // Mirror the production placeholder for the one formatted key used in
        // these tests, so QString::arg() is exercised for real.
        if (key == QLatin1String("ai.http_error"))
            return QStringLiteral("ai.http_error %1");
        return key;
    }
    QString translate(const char* key) const override
    {
        return translate(QString::fromUtf8(key));
    }
    QString displayName() const override { return QStringLiteral("English"); }
    QString flagEmoji() const override { return QStringLiteral("EN"); }
};

class FakeAppPaths : public IAppPaths {
public:
    explicit FakeAppPaths(QString root) : m_root(std::move(root)) {}

    QString appDir() const override { return m_root; }
    QString projectRootDir() const override { return m_root; }
    QString dataRootDir() const override { return m_root; }
    QString configPath() const override { return m_root + "/Config.ini"; }
    QString logsDir() const override { return m_root + "/logs"; }
    QString modelsDir() const override { return m_root + "/models"; }
    QString predictDir(const QString& type) const override
    {
        return m_root + "/predict/" + type;
    }
    QString aiAssistantDir() const override { return m_root + "/AIAssistant"; }
    QString aiComputerVisionDir() const override
    {
        return m_root + "/AIComputerVision";
    }
    QString uploadDir() const override { return m_root + "/uploads"; }
    QString thumbnailsDir() const override { return m_root + "/thumbnails"; }
    QString pluginsDir() const override { return m_root + "/plugins"; }

private:
    QString m_root;
};

/// Minimal QNetworkReply with a canned body/error; completed by the manager.
class FakeReply : public QNetworkReply {
public:
    FakeReply(const QNetworkRequest& request, QByteArray body,
              QNetworkReply::NetworkError error, const QString& errorText,
              QObject* parent)
        : QNetworkReply(parent), m_body(std::move(body))
    {
        setRequest(request);
        setUrl(request.url());
        setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
        if (error != QNetworkReply::NoError) setError(error, errorText);
        open(QIODevice::ReadOnly | QIODevice::Unbuffered);
    }

    void abort() override {}

    qint64 bytesAvailable() const override
    {
        return qint64(m_body.size()) - m_offset +
               QNetworkReply::bytesAvailable();
    }

protected:
    qint64 readData(char* data, qint64 maxlen) override
    {
        const qint64 remaining = qint64(m_body.size()) - m_offset;
        if (remaining <= 0) return -1;
        const qint64 count = qMin(maxlen, remaining);
        std::memcpy(data, m_body.constData() + m_offset, size_t(count));
        m_offset += count;
        return count;
    }

private:
    QByteArray m_body;
    qint64 m_offset = 0;
};

class FakeNetworkManager : public QNetworkAccessManager {
public:
    QByteArray replyBody =
        R"({"choices":[{"message":{"content":"Mock answer"}}]})";
    QNetworkReply::NetworkError replyError = QNetworkReply::NoError;
    QList<QNetworkRequest> requests;
    QList<QByteArray> requestBodies;

protected:
    QNetworkReply* createRequest(Operation operation,
                                 const QNetworkRequest& request,
                                 QIODevice* outgoingData) override
    {
        Q_UNUSED(operation);
        requests.append(request);
        requestBodies.append(outgoingData ? outgoingData->readAll()
                                          : QByteArray());

        auto* reply =
            new FakeReply(request, replyBody, replyError, "mock error", this);
        // QNetworkAccessManager::finished is the signal AIAssistant consumes;
        // emit it on the next event-loop turn, after the caller has registered
        // the reply as pending.
        QTimer::singleShot(0, this, [this, reply]() { emit finished(reply); });
        return reply;
    }
};

class TestableAIAssistant : public AIAssistant {
public:
    using AIAssistant::AIAssistant;
    using AIAssistant::markServerReady;
};

namespace {
constexpr int kTestPort = 18099;

struct Fixture {
    QTemporaryDir dir;
    FakeAppPaths paths{dir.path()};
    FakeLanguageService language;
    FakeNetworkManager network;
    std::unique_ptr<TestableAIAssistant> assistant;

    Fixture()
    {
        Q_ASSERT(dir.isValid());
        assistant = std::make_unique<TestableAIAssistant>(
            &language, nullptr, &paths, QStringLiteral("python"), kTestPort,
            nullptr, &network);
    }
};
} // namespace

class TestAIAssistant : public QObject {
    Q_OBJECT

private slots:
    void constructorCreatesDefaultSession();
    void newChatAndDeleteKeepCurrentInvariant();
    void sendMessageWaitsForServerReadyThenUsesInjectedPort();
    void adminEndpointsHonourInjectedPort();
    void httpErrorBecomesAssistantMessage();
    void missingServerScriptEmitsError();
    void invalidTargetsAreNoOps();
    void sessionsPersistAcrossInstances();
};

void TestAIAssistant::constructorCreatesDefaultSession()
{
    Fixture f;
    QCOMPARE(f.assistant->getSessions().size(), 1);
    QVERIFY(!f.assistant->currentSessionId().isEmpty());
    QVERIFY(f.assistant->getSessions().first().titleIsDefault);
    QVERIFY(f.assistant->getSessions().first().title.startsWith(
        QStringLiteral("ai.new_session")));
    QVERIFY(!f.assistant->isThinking());
    QVERIFY(!f.assistant->isServerRunning());
}

void TestAIAssistant::newChatAndDeleteKeepCurrentInvariant()
{
    Fixture f;
    auto& assistant = *f.assistant;

    const QString firstId = assistant.currentSessionId();
    assistant.newChat();
    QCOMPARE(assistant.getSessions().size(), 2);
    QVERIFY(assistant.currentSessionId() != firstId);

    const QString secondId = assistant.currentSessionId();
    assistant.loadSession(firstId);
    QCOMPARE(assistant.currentSessionId(), firstId);
    assistant.loadSession(QStringLiteral("missing"));
    QCOMPARE(assistant.currentSessionId(), firstId);

    assistant.deleteSession(firstId);
    QCOMPARE(assistant.getSessions().size(), 1);
    QCOMPARE(assistant.currentSessionId(), secondId);

    // Deleting the last session must always leave a usable current session.
    assistant.deleteSession(secondId);
    QCOMPARE(assistant.getSessions().size(), 1);
    QVERIFY(!assistant.currentSessionId().isEmpty());
}

void TestAIAssistant::sendMessageWaitsForServerReadyThenUsesInjectedPort()
{
    Fixture f;
    auto& assistant = *f.assistant;
    QSignalSpy historyChanged(&assistant,
                              &IAIAssistantService::historyChanged);

    assistant.sendMessage(QStringLiteral("Hello AI"));

    // User message + auto-title happen immediately; the request is queued
    // until the backend reports ready.
    QCOMPARE(assistant.getHistory().size(), 1);
    QCOMPARE(assistant.getSessions().first().title, QStringLiteral("Hello AI"));
    QVERIFY(assistant.isThinking());
    QCOMPARE(f.network.requests.size(), 0);

    assistant.markServerReady();
    QVERIFY(assistant.isServerRunning());

    QTRY_COMPARE(f.network.requests.size(), 1);
    QTRY_COMPARE(assistant.getHistory().size(), 2);

    QCOMPARE(f.network.requests.first().url().toString(),
             QStringLiteral("http://127.0.0.1:18099/v1/chat/completions"));

    const QJsonObject payload =
        QJsonDocument::fromJson(f.network.requestBodies.first()).object();
    QCOMPARE(payload.value("messages").toArray().size(), 1);
    QCOMPARE(payload.value("language").toString(), QStringLiteral("en"));
    QCOMPARE(payload.value("max_tokens").toInt(),
             AppConstants::AIServer::DEFAULT_MAX_TOKENS);

    const QJsonObject reply = assistant.getHistory().last();
    QCOMPARE(reply.value("role").toString(), QStringLiteral("assistant"));
    QCOMPARE(reply.value("content").toString(), QStringLiteral("Mock answer"));
    QVERIFY(!assistant.isThinking());
    QVERIFY(historyChanged.count() >= 2);
}

void TestAIAssistant::adminEndpointsHonourInjectedPort()
{
    Fixture f;
    auto& assistant = *f.assistant;
    assistant.markServerReady();

    assistant.restartModel();
    assistant.restartRAG();
    assistant.restartAgent();
    QTRY_COMPARE(f.network.requests.size(), 3);

    QCOMPARE(f.network.requests.at(0).url().toString(),
             QStringLiteral("http://127.0.0.1:18099/admin/reload-model"));
    QCOMPARE(f.network.requests.at(1).url().toString(),
             QStringLiteral("http://127.0.0.1:18099/admin/reload-rag"));
    QCOMPARE(f.network.requests.at(2).url().toString(),
             QStringLiteral("http://127.0.0.1:18099/admin/reload-agent"));
}

void TestAIAssistant::httpErrorBecomesAssistantMessage()
{
    Fixture f;
    auto& assistant = *f.assistant;
    f.network.replyError = QNetworkReply::InternalServerError;
    f.network.replyBody = R"({"detail":"boom"})";
    assistant.markServerReady();

    assistant.sendMessage(QStringLiteral("Hi"));
    QTRY_COMPARE(assistant.getHistory().size(), 2);

    const QJsonObject last = assistant.getHistory().last();
    QCOMPARE(last.value("role").toString(), QStringLiteral("assistant"));
    // FakeLanguageService returns the key plus its %1 placeholder.
    QCOMPARE(last.value("content").toString(),
             QStringLiteral("ai.http_error boom"));
    QVERIFY(!assistant.isThinking());
}

void TestAIAssistant::missingServerScriptEmitsError()
{
    Fixture f;
    auto& assistant = *f.assistant;
    QSignalSpy errors(&assistant, &IAIAssistantService::errorOccurred);

    // The temp AIAssistant dir has no server_manager.py: startServer must fail
    // fast with the translated message instead of spawning a process.
    assistant.startServer(0);
    QCOMPARE(errors.count(), 1);
    QCOMPARE(errors.first().first().toString(),
             QStringLiteral("ai.missing_script"));
    QVERIFY(!assistant.isServerRunning());
}

void TestAIAssistant::invalidTargetsAreNoOps()
{
    Fixture f;
    auto& assistant = *f.assistant;
    const int before = assistant.getHistory().size();
    const QString current = assistant.currentSessionId();

    assistant.sendMessageToSession(QStringLiteral("missing"),
                                   QStringLiteral("x"));
    assistant.retryMessage(QStringLiteral("missing"), 0);
    assistant.editMessage(QStringLiteral("missing"), 0, QStringLiteral("x"));
    assistant.retryAgentTask(QStringLiteral("missing"), 0);
    assistant.sendMessage(QString());

    QCOMPARE(assistant.getHistory().size(), before);
    QCOMPARE(assistant.currentSessionId(), current);
    QVERIFY(!assistant.isThinking());
}

void TestAIAssistant::sessionsPersistAcrossInstances()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    FakeAppPaths paths(dir.path());
    FakeLanguageService language;
    FakeNetworkManager network;

    QString firstId;
    {
        TestableAIAssistant first(&language, nullptr, &paths,
                                  QStringLiteral("python"), kTestPort,
                                  nullptr, &network);
        firstId = first.getSessions().first().id;
        first.newChat();
        first.sendMessage(QStringLiteral("persist me"));
        QCOMPARE(first.getHistory().size(), 1);
    }

    FakeNetworkManager network2;
    TestableAIAssistant second(&language, nullptr, &paths,
                               QStringLiteral("python"), kTestPort,
                               nullptr, &network2);
    QCOMPARE(second.getSessions().size(), 2);
    QCOMPARE(second.currentSessionId(), second.getSessions().last().id);
    QCOMPARE(second.getSessions().first().id, firstId);
    QVERIFY(second.getSessions().first().titleIsDefault);
    QCOMPARE(second.getSessions().last().messages.size(), 1);
    QCOMPARE(second.getSessions().last().messages.first().value("content").toString(),
             QStringLiteral("persist me"));
}

QTEST_MAIN(TestAIAssistant)
#include "TestAIAssistant.moc"
