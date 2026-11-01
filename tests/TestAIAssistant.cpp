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
#include <QDir>
#include <QFile>

#include <cstring>
#include <memory>
#include <utility>

#include "AIAssistant.h"
#include "AIAssistantPayloadBuilder.h"
#include "AgentActionDispatcher.h"
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
    void complete() { emit finished(); }

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
        QTimer::singleShot(0, this, [this, reply]() {
            reply->complete();
            emit finished(reply);
        });
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

/// Minimal manifest used by the agent-action tests: one alias-capable action,
/// one action with a required enum, and no sample fixtures.
bool writeAgentManifest(const QTemporaryDir &dir)
{
    if (!QDir().mkpath(dir.path() + "/Config")) return false;
    QFile file(dir.path() + "/Config/agent_action_manifest.json");
    if (!file.open(QIODevice::WriteOnly)) return false;
    file.write(R"({
        "version": 3,
        "actions": [
            {"id": "viewer.load_2d", "aliases": ["viewer.load"],
             "parameters": {"path": {"type": "string", "required": false}}},
            {"id": "viewer.load_3d", "parameters": {}},
            {"id": "language.change",
             "parameters": {"language": {"type": "string", "enum": ["vi", "en"], "required": true}}}
        ],
        "sample_data": {}
    })");
    return true;
}

QJsonObject uiActionStep(const QString &requestId, const QString &action)
{
    QJsonObject params;
    params["request_id"] = requestId;
    params["action"] = action;

    QJsonObject step;
    step["type"] = "tool_call";
    step["tool"] = "application_action";
    step["params"] = params;
    return step;
}

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
            &language, nullptr, nullptr, &paths, QStringLiteral("python"), kTestPort,
            nullptr, &network);
    }
};
} // namespace

class TestAIAssistant : public QObject {
    Q_OBJECT

private slots:
    void constructorCreatesDefaultSession();
    void newChatAndDeleteKeepCurrentInvariant();
    void renameSessionStopsAutoTitle();
    void sendMessageWaitsForServerReadyThenUsesInjectedPort();
    void executeAgentTaskRegistersWorkBeforeRepaint();
    void cancelSessionRequestStopsOnlyThatWork();
    void adminEndpointsHonourInjectedPort();
    void adminEndpointsForwardSuccessAndErrors();
    void adminEndpointsRefuseWhenServerIsNotReady();
    void httpErrorBecomesAssistantMessage();
    void missingServerScriptEmitsError();
    void invalidTargetsAreNoOps();
    void sessionsPersistAcrossInstances();
    void payloadBuilderFiltersUiOnlyRoles();
    void payloadBuilderAgentHistoryExcludesTaskAndTrace();
    void completionContentToleratesMissingChoices();
    void agentDispatcherIgnoresResponseWithoutRequestId();
    void agentDispatcherDispatchesOnlyMatchingRequestId();
    void agentDispatcherAckOnlyRunsNewFollowUp();
    void agentDispatcherRejectsInvalidAction();
    void agentResponseWithoutRequestIdNeverReplaysHistory();
    void agentResponseDispatchesMatchingActionOnce();
    void agentActionRejectionSendsFailureAck();
    void privilegedRequestsCarryOneProcessBearer();
};

void TestAIAssistant::privilegedRequestsCarryOneProcessBearer()
{
    Fixture f;
    auto &assistant = *f.assistant;
    assistant.markServerReady();

    assistant.restartModel();
    assistant.restartRAG();
    assistant.restartAgent();
    assistant.sendMessage(QStringLiteral("authenticate me"));
    QTRY_COMPARE(f.network.requests.size(), 4);

    QByteArray bearer;
    for (const QNetworkRequest &request : std::as_const(f.network.requests)) {
        const QByteArray header = request.rawHeader("Authorization");
        QVERIFY(header.startsWith("Bearer "));
        QCOMPARE(header.size(), 71); // prefix + 64 lowercase hex characters
        if (bearer.isEmpty()) bearer = header;
        QCOMPARE(header, bearer);
    }
}

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

void TestAIAssistant::renameSessionStopsAutoTitle()
{
    Fixture f;
    auto& assistant = *f.assistant;
    const QString id = assistant.currentSessionId();

    assistant.renameSession(id, QStringLiteral("  My chat  "));
    QCOMPARE(assistant.getSessions().first().title, QStringLiteral("My chat"));
    QVERIFY(!assistant.getSessions().first().titleIsDefault);

    // A later prompt must not overwrite the name the user chose.
    assistant.sendMessage(QStringLiteral("Do not rename me"));
    QCOMPARE(assistant.getSessions().first().title, QStringLiteral("My chat"));

    // A blank name and an unknown id are no-ops.
    assistant.renameSession(id, QStringLiteral("   "));
    assistant.renameSession(QStringLiteral("missing"), QStringLiteral("Other"));
    QCOMPARE(assistant.getSessions().first().title, QStringLiteral("My chat"));
}

void TestAIAssistant::executeAgentTaskRegistersWorkBeforeRepaint()
{
    Fixture f;
    auto& assistant = *f.assistant;

    // The UI reads isSessionThinking() inside the historyChanged slot. Work
    // must therefore be registered BEFORE the signal, or the chat is repainted
    // as idle and never shows the "AI đang suy nghĩ..." marker.
    QList<bool> thinkingAtRepaint;
    connect(&assistant, &IAIAssistantService::historyChanged, this, [&]() {
        thinkingAtRepaint.append(
            assistant.isSessionThinking(assistant.currentSessionId()));
    });

    assistant.executeAgentTask(assistant.currentSessionId(),
                               QStringLiteral("Explain the project"));

    QVERIFY(!thinkingAtRepaint.isEmpty());
    QVERIFY2(thinkingAtRepaint.first(),
             "the first repaint after Send was rendered as idle");
    QVERIFY(assistant.isThinking());

    // The sidebar title follows the first prompt, not the placeholder.
    QCOMPARE(assistant.getSessions().first().title,
             QStringLiteral("Explain the project"));
    QVERIFY(!assistant.getSessions().first().titleIsDefault);

    assistant.markServerReady();
    QTRY_COMPARE(f.network.requests.size(), 1);
    QTRY_VERIFY(!assistant.isThinking());
    QVERIFY(!thinkingAtRepaint.last());
}

void TestAIAssistant::cancelSessionRequestStopsOnlyThatWork()
{
    Fixture f;
    auto& assistant = *f.assistant;
    const QString sessionId = assistant.currentSessionId();

    // A locally queued request has not reached the backend, so cancellation
    // removes it without manufacturing a remote cancel for a run that does
    // not exist yet.
    assistant.executeAgentTask(sessionId, QStringLiteral("queued task"));
    QVERIFY(assistant.isSessionThinking(sessionId));
    assistant.cancelSessionRequest(sessionId);
    QVERIFY(!assistant.isSessionThinking(sessionId));
    QVERIFY(!assistant.isThinking());
    QCOMPARE(f.network.requests.size(), 0);

    assistant.markServerReady();
    assistant.executeAgentTask(sessionId, QStringLiteral("active task"));
    QCOMPARE(f.network.requests.size(), 1);
    QCOMPARE(f.network.requests.first().url().path(),
             QStringLiteral("/v1/agent/execute"));

    assistant.cancelSessionRequest(sessionId);
    QVERIFY(!assistant.isSessionThinking(sessionId));
    QVERIFY(!assistant.isThinking());
    QCOMPARE(f.network.requests.size(), 2);
    QCOMPARE(f.network.requests.last().url().path(),
             QStringLiteral("/v1/agent/cancel"));
    const QJsonObject cancelPayload =
        QJsonDocument::fromJson(f.network.requestBodies.last()).object();
    QCOMPARE(cancelPayload.value("session_id").toString(), sessionId);

    // Late completion of the aborted reply is ignored: Stop must not append a
    // transport error or the stale model response.
    QCoreApplication::processEvents();
    QCOMPARE(assistant.getHistory().size(), 2);
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
    // The placeholder flag must be cleared, otherwise the sidebar keeps
    // displaying "New Session <date>" instead of the prompt.
    QVERIFY(!assistant.getSessions().first().titleIsDefault);
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

    const ChatMessage reply = assistant.getHistory().last();
    QCOMPARE(reply.role, ChatRole::Assistant);
    QCOMPARE(reply.content, QStringLiteral("Mock answer"));
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

void TestAIAssistant::adminEndpointsForwardSuccessAndErrors()
{
    Fixture f;
    auto &assistant = *f.assistant;
    assistant.markServerReady();
    QSignalSpy statuses(&assistant, &IAIAssistantService::serverStatusChanged);
    QSignalSpy errors(&assistant, &IAIAssistantService::errorOccurred);

    f.network.replyBody = R"({"message":"Model ready"})";
    assistant.restartModel();
    QTRY_VERIFY(statuses.count() >= 2);
    QCOMPARE(statuses.last().first().toString(), QStringLiteral("✅ Model ready"));

    f.network.replyError = QNetworkReply::InternalServerError;
    assistant.restartRAG();
    QTRY_COMPARE(errors.count(), 1);
    QVERIFY(errors.first().first().toString().contains(QStringLiteral("ai.restart_rag_failed")));
}

void TestAIAssistant::adminEndpointsRefuseWhenServerIsNotReady()
{
    Fixture f;
    auto &assistant = *f.assistant;
    QSignalSpy errors(&assistant, &IAIAssistantService::errorOccurred);

    assistant.restartModel();
    assistant.restartRAG();
    assistant.restartAgent();

    QCOMPARE(f.network.requests.size(), 0);
    QCOMPARE(errors.count(), 3);
    for (const QList<QVariant> &arguments : errors) {
        QCOMPARE(arguments.first().toString(), QStringLiteral("ai.server_not_running"));
    }
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

    const ChatMessage last = assistant.getHistory().last();
    QCOMPARE(last.role, ChatRole::Assistant);
    // FakeLanguageService returns the key plus its %1 placeholder.
    QCOMPARE(last.content, QStringLiteral("ai.http_error boom"));
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
        TestableAIAssistant first(&language, nullptr, nullptr, &paths,
                                  QStringLiteral("python"), kTestPort,
                                  nullptr, &network);
        firstId = first.getSessions().first().id;
        first.newChat();
        first.sendMessage(QStringLiteral("persist me"));
        QCOMPARE(first.getHistory().size(), 1);
    }

    FakeNetworkManager network2;
    TestableAIAssistant second(&language, nullptr, nullptr, &paths,
                               QStringLiteral("python"), kTestPort,
                               nullptr, &network2);
    QCOMPARE(second.getSessions().size(), 2);
    QCOMPARE(second.currentSessionId(), second.getSessions().last().id);
    QCOMPARE(second.getSessions().first().id, firstId);
    QVERIFY(second.getSessions().first().titleIsDefault);
    QCOMPARE(second.getSessions().last().messages.size(), 1);
    QCOMPARE(second.getSessions().last().messages.first().content,
             QStringLiteral("persist me"));
}

void TestAIAssistant::payloadBuilderFiltersUiOnlyRoles()
{
    ChatMessage user;
    user.role = ChatRole::User;
    user.content = QStringLiteral("hi");

    ChatMessage trace;
    trace.role = ChatRole::AgentTrace;
    trace.content = QStringLiteral(R"({"steps":[]})");

    ChatMessage assistant;
    assistant.role = ChatRole::Assistant;
    assistant.content = QStringLiteral("hello");

    const QJsonObject payload = AIAssistantPayloadBuilder::buildCompletionPayload(
        {user, trace, assistant}, 0.4, QStringLiteral("vi"));

    const QJsonArray sent = payload.value("messages").toArray();
    QCOMPARE(sent.size(), 2);
    QCOMPARE(sent.at(0).toObject().value("content").toString(),
             QStringLiteral("hi"));
    QCOMPARE(sent.at(1).toObject().value("role").toString(),
             QStringLiteral("assistant"));
    QCOMPARE(payload.value("temperature").toDouble(), 0.4);
    QCOMPARE(payload.value("language").toString(), QStringLiteral("vi"));
    QCOMPARE(payload.value("max_tokens").toInt(),
             AppConstants::AIServer::DEFAULT_MAX_TOKENS);
}

void TestAIAssistant::payloadBuilderAgentHistoryExcludesTaskAndTrace()
{
    ChatMessage first;
    first.role = ChatRole::User;
    first.content = QStringLiteral("first");
    first.attachments = {QStringLiteral("a.png")};

    ChatMessage answer;
    answer.role = ChatRole::Assistant;
    answer.content = QStringLiteral("answer");

    ChatMessage trace;
    trace.role = ChatRole::AgentTrace;
    trace.content = QStringLiteral(R"({"steps":[]})");

    ChatMessage task;
    task.role = ChatRole::User;
    task.content = QStringLiteral("task");

    const QJsonObject payload = AIAssistantPayloadBuilder::buildAgentPayload(
        {first, answer, trace, task}, QStringLiteral("task"),
        {QStringLiteral("b.png")}, 0.3, QStringLiteral("en"));

    const QJsonArray history = payload.value("history").toArray();
    QCOMPARE(history.size(), 2);
    QCOMPARE(history.at(0).toObject().value("content").toString(),
             QStringLiteral("first"));
    QCOMPARE(history.at(0).toObject().value("attachments").toArray().size(), 1);
    QCOMPARE(history.at(1).toObject().value("content").toString(),
             QStringLiteral("answer"));
    QCOMPARE(payload.value("task").toString(), QStringLiteral("task"));
    QCOMPARE(payload.value("attachments").toArray().size(), 1);
    QCOMPARE(payload.value("temperature").toDouble(), 0.3);
    QCOMPARE(payload.value("language").toString(), QStringLiteral("en"));
}

void TestAIAssistant::completionContentToleratesMissingChoices()
{
    QCOMPARE(AIAssistantPayloadBuilder::completionContent(QJsonObject()),
             QString());

    QJsonObject message;
    message["content"] = QStringLiteral("answer");
    QJsonObject choice;
    choice["message"] = message;
    QJsonObject response;
    response["choices"] = QJsonArray{choice};

    QCOMPARE(AIAssistantPayloadBuilder::completionContent(response),
             QStringLiteral("answer"));
}

void TestAIAssistant::agentDispatcherIgnoresResponseWithoutRequestId()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QVERIFY(writeAgentManifest(dir));
    FakeAppPaths paths(dir.path());
    AgentActionDispatcher dispatcher(&paths);
    QSignalSpy requested(&dispatcher, &AgentActionDispatcher::uiActionRequested);

    // The steps array is history when the response names no request: running
    // them would replay an action the desktop already executed.
    QJsonObject response;
    response["status"] = QStringLiteral("pending_ui_action");
    response["steps"] = QJsonArray{
        uiActionStep(QStringLiteral("req-1"), QStringLiteral("viewer.load_2d")),
        // A step without an id could never be acknowledged; it must not run
        // just because the response id is empty too.
        uiActionStep(QString(), QStringLiteral("viewer.load_2d")),
    };
    dispatcher.dispatch(response, false, QString(), QStringLiteral("sess-1"));

    QCOMPARE(requested.count(), 0);
    QCOMPARE(dispatcher.pendingCount(), 0);
}

void TestAIAssistant::agentDispatcherDispatchesOnlyMatchingRequestId()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QVERIFY(writeAgentManifest(dir));
    FakeAppPaths paths(dir.path());
    AgentActionDispatcher dispatcher(&paths);
    QSignalSpy requested(&dispatcher, &AgentActionDispatcher::uiActionRequested);

    QJsonObject response;
    response["status"] = QStringLiteral("pending_ui_action");
    response["request_id"] = QStringLiteral("req-1");
    response["steps"] = QJsonArray{
        uiActionStep(QStringLiteral("stale"), QStringLiteral("viewer.load_3d")),
        uiActionStep(QStringLiteral("req-1"), QStringLiteral("viewer.load")),
        uiActionStep(QStringLiteral("req-1"), QStringLiteral("viewer.load")),
    };
    dispatcher.dispatch(response, false, QString(), QStringLiteral("sess-1"), 4);

    // One action, canonicalized through the manifest alias.
    QCOMPARE(requested.count(), 1);
    QCOMPARE(requested.first().at(0).toString(), QStringLiteral("viewer.load_2d"));
    const QVariantMap params = requested.first().at(1).toMap();
    QCOMPARE(params.value("action").toString(), QStringLiteral("viewer.load_2d"));

    // The ACK is routed to the session that asked for it and inserted after
    // the response that carried the action.
    QCOMPARE(dispatcher.pendingCount(), 1);
    const auto pending = dispatcher.takePending(QStringLiteral("req-1"));
    QCOMPARE(pending.first, QStringLiteral("sess-1"));
    QCOMPARE(pending.second, 5);
    QCOMPARE(dispatcher.pendingCount(), 0);
}

void TestAIAssistant::agentDispatcherAckOnlyRunsNewFollowUp()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QVERIFY(writeAgentManifest(dir));
    FakeAppPaths paths(dir.path());
    AgentActionDispatcher dispatcher(&paths);
    QSignalSpy requested(&dispatcher, &AgentActionDispatcher::uiActionRequested);

    // A plain ACK response is the continuation of the action just reported:
    // its history contains that action and must not run again.
    QJsonObject completed;
    completed["status"] = QStringLiteral("completed");
    completed["request_id"] = QStringLiteral("req-1");
    completed["steps"] = QJsonArray{uiActionStep(QStringLiteral("req-1"),
                                                 QStringLiteral("viewer.load_2d"))};
    dispatcher.dispatch(completed, true, QStringLiteral("req-1"), QStringLiteral("sess-1"));
    QCOMPARE(requested.count(), 0);

    // A chained workflow parks a NEW action with a different request id.
    QJsonObject chained = completed;
    chained["status"] = QStringLiteral("pending_ui_action");
    chained["request_id"] = QStringLiteral("req-2");
    chained["steps"] = QJsonArray{
        uiActionStep(QStringLiteral("req-1"), QStringLiteral("viewer.load_2d")),
        uiActionStep(QStringLiteral("req-2"), QStringLiteral("viewer.load_2d")),
    };
    dispatcher.dispatch(chained, true, QStringLiteral("req-1"), QStringLiteral("sess-1"));
    QCOMPARE(requested.count(), 1);
}

void TestAIAssistant::agentDispatcherRejectsInvalidAction()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QVERIFY(writeAgentManifest(dir));
    FakeAppPaths paths(dir.path());
    AgentActionDispatcher dispatcher(&paths);
    QSignalSpy requested(&dispatcher, &AgentActionDispatcher::uiActionRequested);
    QSignalSpy rejected(&dispatcher, &AgentActionDispatcher::uiActionRejected);

    QJsonObject response;
    response["status"] = QStringLiteral("pending_ui_action");
    response["request_id"] = QStringLiteral("req-3");
    response["steps"] = QJsonArray{uiActionStep(QStringLiteral("req-3"),
                                                QStringLiteral("language.change"))};
    dispatcher.dispatch(response, false, QString(), QStringLiteral("sess-1"));

    QCOMPARE(requested.count(), 0);
    QCOMPARE(rejected.count(), 1);
    QCOMPARE(rejected.first().at(0).toString(), QStringLiteral("req-3"));
    QVERIFY(rejected.first().at(1).toString().contains(QStringLiteral("requires")));
    // The rejection still needs an ACK back to the server.
    QCOMPARE(dispatcher.pendingCount(), 1);
}

void TestAIAssistant::agentResponseWithoutRequestIdNeverReplaysHistory()
{
    Fixture f;
    QVERIFY(writeAgentManifest(f.dir));
    auto& assistant = *f.assistant;
    QSignalSpy requested(&assistant, &IAIAssistantService::agentUiActionRequested);
    assistant.markServerReady();

    // The final response of an agent run repeats its step history but names
    // no request. Dispatching it used to replay the earlier UI action.
    QJsonObject response;
    response["status"] = QStringLiteral("completed");
    response["steps"] = QJsonArray{uiActionStep(QStringLiteral("old"),
                                                QStringLiteral("viewer.load_2d"))};
    f.network.replyBody = QJsonDocument(response).toJson(QJsonDocument::Compact);

    assistant.executeAgentTask(assistant.currentSessionId(),
                               QStringLiteral("open it"));
    QTRY_COMPARE(f.network.requests.size(), 1);
    QTRY_VERIFY(!assistant.isThinking());

    QCOMPARE(requested.count(), 0);
}

void TestAIAssistant::agentResponseDispatchesMatchingActionOnce()
{
    Fixture f;
    QVERIFY(writeAgentManifest(f.dir));
    auto& assistant = *f.assistant;
    QSignalSpy requested(&assistant, &IAIAssistantService::agentUiActionRequested);
    assistant.markServerReady();

    QJsonObject response;
    response["status"] = QStringLiteral("pending_ui_action");
    response["request_id"] = QStringLiteral("new");
    response["steps"] = QJsonArray{
        uiActionStep(QStringLiteral("old"), QStringLiteral("viewer.load_2d")),
        uiActionStep(QStringLiteral("new"), QStringLiteral("viewer.load_2d")),
    };
    f.network.replyBody = QJsonDocument(response).toJson(QJsonDocument::Compact);

    assistant.executeAgentTask(assistant.currentSessionId(),
                               QStringLiteral("open it"));
    QTRY_COMPARE(requested.count(), 1);
    QCOMPARE(requested.first().at(0).toString(), QStringLiteral("viewer.load_2d"));
}

void TestAIAssistant::agentActionRejectionSendsFailureAck()
{
    Fixture f;
    QVERIFY(writeAgentManifest(f.dir));
    auto& assistant = *f.assistant;
    assistant.markServerReady();

    // `language.change` requires a language: the manifest rejects the step, so
    // the desktop must ACK the failure instead of emitting a broken action.
    QJsonObject response;
    response["status"] = QStringLiteral("pending_ui_action");
    response["request_id"] = QStringLiteral("bad");
    response["steps"] = QJsonArray{uiActionStep(QStringLiteral("bad"),
                                                QStringLiteral("language.change"))};
    f.network.replyBody = QJsonDocument(response).toJson(QJsonDocument::Compact);

    const QString sessionId = assistant.currentSessionId();
    assistant.executeAgentTask(sessionId, QStringLiteral("change language"));
    QTRY_COMPARE(f.network.requests.size(), 2);

    QCOMPARE(f.network.requests.at(1).url().toString(),
             QStringLiteral("http://127.0.0.1:18099/v1/agent/ui-action-result"));
    const QJsonObject ack =
        QJsonDocument::fromJson(f.network.requestBodies.at(1)).object();
    QCOMPARE(ack.value("request_id").toString(), QStringLiteral("bad"));
    QCOMPARE(ack.value("success").toBool(), false);
    QCOMPARE(ack.value("session_id").toString(), sessionId);
    QVERIFY(ack.value("result").toObject().value("error").toString()
                .contains(QStringLiteral("requires")));
}

QTEST_MAIN(TestAIAssistant)
#include "TestAIAssistant.moc"
