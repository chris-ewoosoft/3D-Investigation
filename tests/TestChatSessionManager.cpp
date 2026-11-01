#include <QtTest>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonObject>
#include <QTemporaryDir>

#include "ChatSessionManager.h"
#include "SecretVault.h"

/**
 * @brief ChatSessionManager contract: current-session rules, attachment
 *        cleanup on remove() and the ChatSessionStore round trip.
 *
 * The manager is the only place that owns the session list, so these rules are
 * verified here instead of through AIAssistant (which needs a network stack and
 * a server process).
 */
class TestChatSessionManager : public QObject {
    Q_OBJECT

private slots:
    void createMakesNewSessionCurrent();
    void setCurrentRejectsUnknownId();
    void renameReplacesThePlaceholderTitle();
    void adoptPromptTitlesRepairsLegacyPlaceholders();
    void removeDeletesAttachmentsAndFallsBack();
    void removingLastSessionLeavesNoCurrent();
    void replaceAllSelectsLastSession();
    void saveAndLoadRoundTrip();
    void legacyPlaintextHistoryStillLoads();
    void unreadableHistoryYieldsNoSessions();
    void missingHistoryYieldsNoSessions();
    void corruptHistoryYieldsNoSessions();
    void aFailedSealKeepsThePreviousHistory();
    void anUnwritableHistoryPathKeepsThePreviousHistory();
    void theTypedMessageRoundTripsThroughTheWireFormat();
    void theAgentTraceIsNeverAModelRole();

private:
    static bool writeFile(const QString &path) {
        QFile f(path);
        if (!f.open(QIODevice::WriteOnly)) return false;
        f.write("x");
        return true;
    }

    // The stored history must be sealed: the DPAPI blob ("enc1:") on Windows,
    // the vault's explicitly marked fallback ("plain:") elsewhere. Either way
    // the raw file must never carry the readable message text.
    static void verifySealed(const QByteArray &raw, const QString &secret) {
        QVERIFY(!raw.isEmpty());
#ifdef Q_OS_WIN
        QVERIFY(raw.startsWith("enc1:"));
        QVERIFY(!raw.contains(secret.toUtf8()));
#else
        QVERIFY(raw.startsWith("plain:"));
        QVERIFY(raw.contains(secret.toUtf8()));
#endif
    }
};

void TestChatSessionManager::createMakesNewSessionCurrent()
{
    ChatSessionManager sessions;
    QVERIFY(sessions.isEmpty());
    QVERIFY(sessions.currentId().isEmpty());
    QVERIFY(sessions.current() == nullptr);

    ChatSession &first = sessions.create("First");
    QVERIFY(!first.id.isEmpty());
    QVERIFY(first.createdAt.isValid());
    QCOMPARE(sessions.sessions().size(), 1);
    QCOMPARE(sessions.currentId(), first.id);
    QVERIFY(sessions.current() == &sessions.sessions().first());

    const QString firstId = first.id;
    ChatSession &second = sessions.create("Second");
    QCOMPARE(sessions.sessions().size(), 2);
    QCOMPARE(sessions.currentId(), second.id);
    QCOMPARE(sessions.find(firstId)->title, QString("First"));
    QVERIFY(sessions.find("missing") == nullptr);
}

void TestChatSessionManager::setCurrentRejectsUnknownId()
{
    ChatSessionManager sessions;
    const QString id = sessions.create("Only").id;
    sessions.create("Other");

    QVERIFY(!sessions.setCurrent("missing"));
    QCOMPARE(sessions.currentId(), sessions.sessions().last().id);

    QVERIFY(sessions.setCurrent(id));
    QCOMPARE(sessions.currentId(), id);
    QCOMPARE(sessions.current()->title, QString("Only"));
}

void TestChatSessionManager::renameReplacesThePlaceholderTitle()
{
    ChatSessionManager sessions;
    const QString id = sessions.create("Phiên mới 23/10 09:18").id;
    sessions.current()->titleIsDefault = true;

    // The user's name wins, is trimmed, and stops the auto-title from running.
    QVERIFY(sessions.rename(id, QStringLiteral("  My chat  ")));
    QCOMPARE(sessions.find(id)->title, QStringLiteral("My chat"));
    QVERIFY(!sessions.find(id)->titleIsDefault);

    // A blank name must not erase the title, and an unknown id is a no-op.
    QVERIFY(!sessions.rename(id, QStringLiteral("   ")));
    QCOMPARE(sessions.find(id)->title, QStringLiteral("My chat"));
    QVERIFY(!sessions.rename(QStringLiteral("missing"), QStringLiteral("Other")));
    QCOMPARE(sessions.find(id)->title, QStringLiteral("My chat"));
}

void TestChatSessionManager::adoptPromptTitlesRepairsLegacyPlaceholders()
{
    const auto userMessage = [](const QString &text) {
        ChatMessage message;
        message.role = ChatRole::User;
        message.content = text;
        return message;
    };

    // A history written before auto-title cleared the flag: the prompt is
    // already there, but the sidebar still shows the placeholder.
    ChatSession legacy;
    legacy.id = QStringLiteral("legacy");
    legacy.title = QStringLiteral("Phiên mới 23/10 09:18");
    legacy.titleIsDefault = true;
    legacy.messages = {userMessage(QStringLiteral("  Explain the pipeline  "))};

    // A session the user named explicitly must keep that name.
    ChatSession named;
    named.id = QStringLiteral("named");
    named.title = QStringLiteral("My chat");
    named.messages = {userMessage(QStringLiteral("Other prompt"))};

    // No prompt yet: the placeholder is still the correct title.
    ChatSession empty;
    empty.id = QStringLiteral("empty");
    empty.title = QStringLiteral("Phiên mới 23/10 10:00");
    empty.titleIsDefault = true;

    // Neither an assistant-only turn nor a blank prompt is a title source.
    ChatSession assistantOnly;
    assistantOnly.id = QStringLiteral("assistant");
    assistantOnly.title = QStringLiteral("Phiên mới 23/10 10:05");
    assistantOnly.titleIsDefault = true;
    ChatMessage reply;
    reply.role = ChatRole::Assistant;
    reply.content = QStringLiteral("Hello");
    assistantOnly.messages = {reply, userMessage(QStringLiteral("   "))};

    // The repair truncates like auto-title does, so the sidebar stays readable.
    ChatSession longPrompt;
    longPrompt.id = QStringLiteral("long");
    longPrompt.title = QStringLiteral("Phiên mới 23/10 10:10");
    longPrompt.titleIsDefault = true;
    longPrompt.messages = {userMessage(QStringLiteral("12345678901234567890123456789012345"))};

    ChatSessionManager sessions;
    sessions.replaceAll({legacy, named, empty, assistantOnly, longPrompt});

    QVERIFY(sessions.adoptPromptTitles());
    QCOMPARE(sessions.find("legacy")->title, QStringLiteral("Explain the pipeline"));
    QVERIFY(!sessions.find("legacy")->titleIsDefault);
    QCOMPARE(sessions.find("named")->title, QStringLiteral("My chat"));
    QVERIFY(!sessions.find("named")->titleIsDefault);
    QCOMPARE(sessions.find("empty")->title, QStringLiteral("Phiên mới 23/10 10:00"));
    QVERIFY(sessions.find("empty")->titleIsDefault);
    QCOMPARE(sessions.find("assistant")->title, QStringLiteral("Phiên mới 23/10 10:05"));
    QVERIFY(sessions.find("assistant")->titleIsDefault);
    QCOMPARE(sessions.find("long")->title,
             QStringLiteral("123456789012345678901234567890..."));
    QVERIFY(!sessions.find("long")->titleIsDefault);

    // The repair is idempotent: a second pass has nothing left to adopt, so the
    // load path does not rewrite the sealed history on every start.
    QVERIFY(!sessions.adoptPromptTitles());
}

void TestChatSessionManager::removeDeletesAttachmentsAndFallsBack()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    QVERIFY(QDir().mkpath(dir.path() + "/Upload"));
    QVERIFY(QDir().mkpath(dir.path() + "/Thumbnails"));
    const QString image = dir.path() + "/Upload/photo.png";
    const QString imageThumb = dir.path() + "/Thumbnails/photo.png";
    const QString doc = dir.path() + "/Upload/notes.txt";
    const QString docThumb = dir.path() + "/Thumbnails/notes.txt.png";
    QVERIFY(writeFile(image));
    QVERIFY(writeFile(imageThumb));
    QVERIFY(writeFile(doc));
    QVERIFY(writeFile(docThumb));

    ChatSessionManager sessions;
    const QString firstId = sessions.create("First").id;
    ChatSession &current = sessions.create("Second");
    ChatMessage withAttachment;
    withAttachment.role = ChatRole::User;
    withAttachment.attachments = {QString(image), QString(doc)};
    current.messages.append(withAttachment);

    QVERIFY(sessions.remove(current.id));
    QCOMPARE(sessions.sessions().size(), 1);
    // The removed session was current → fall back to the last remaining one.
    QCOMPARE(sessions.currentId(), firstId);
    // Attachment files (upload + thumbnail, image and non-image) are gone.
    QVERIFY(!QFile::exists(image));
    QVERIFY(!QFile::exists(imageThumb));
    QVERIFY(!QFile::exists(doc));
    QVERIFY(!QFile::exists(docThumb));
    QVERIFY(!sessions.remove("missing"));
}

void TestChatSessionManager::removingLastSessionLeavesNoCurrent()
{
    ChatSessionManager sessions;
    const QString id = sessions.create("Only").id;
    QVERIFY(sessions.remove(id));
    QVERIFY(sessions.isEmpty());
    QVERIFY(sessions.currentId().isEmpty());
    QVERIFY(sessions.current() == nullptr);
    QVERIFY(sessions.currentMessages().isEmpty());
}

void TestChatSessionManager::replaceAllSelectsLastSession()
{
    ChatSession a;
    a.id = "a";
    a.title = "A";
    ChatSession b;
    b.id = "b";
    b.title = "B";

    ChatSessionManager sessions;
    sessions.replaceAll({a, b});
    QCOMPARE(sessions.sessions().size(), 2);
    QCOMPARE(sessions.currentId(), QString("b"));

    sessions.replaceAll({});
    QVERIFY(sessions.currentId().isEmpty());
}

void TestChatSessionManager::saveAndLoadRoundTrip()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString configPath = dir.path() + "/settings.ini";

    {
        ChatSessionManager sessions;
        ChatSession &session = sessions.create("Round trip");
        // The marker contains characters that the base64 alphabet cannot
        // produce, so "not in the raw file" is a deterministic check.
        ChatMessage message;
        message.role = ChatRole::User;
        message.content = QStringLiteral("secret-payload-4711");
        session.messages.append(message);
        sessions.save("alice", configPath);
    }

    QFile stored(dir.path() + "/chat_sessions_alice.json");
    QVERIFY(stored.exists());
    QVERIFY(stored.open(QIODevice::ReadOnly));
    const QByteArray raw = stored.readAll();
    stored.close();

    // The history is sealed at rest: the file must not expose the content.
    verifySealed(raw, "secret-payload-4711");

    ChatSessionManager restored;
    restored.load("alice", configPath);
    QCOMPARE(restored.sessions().size(), 1);
    QCOMPARE(restored.sessions().first().title, QString("Round trip"));
    QCOMPARE(restored.currentId(), restored.sessions().first().id);
    const QList<ChatMessage> messages = restored.currentMessages();
    QCOMPARE(messages.size(), 1);
    QCOMPARE(messages.first().content, QString("secret-payload-4711"));

    // Unknown user → empty manager, no current session.
    ChatSessionManager other;
    other.load("bob", configPath);
    QVERIFY(other.isEmpty());
    QVERIFY(other.currentId().isEmpty());
}

void TestChatSessionManager::legacyPlaintextHistoryStillLoads()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString configPath = dir.path() + "/settings.ini";
    const QString historyPath = dir.path() + "/chat_sessions_alice.json";

    // A history written before the vault existed: readable JSON on disk.
    QFile legacy(historyPath);
    QVERIFY(legacy.open(QIODevice::WriteOnly));
    legacy.write("[{\"id\":\"legacy-id\",\"title\":\"Legacy\",\"createdAt\":\"2026-01-01T00:00:00\","
                 "\"titleIsDefault\":false,\"messages\":[{\"role\":\"user\",\"content\":\"old message\"}]}]");
    legacy.close();

    // It still loads (the vault passes legacy plaintext through unchanged).
    ChatSessionManager restored;
    restored.load("alice", configPath);
    QCOMPARE(restored.sessions().size(), 1);
    QCOMPARE(restored.sessions().first().title, QString("Legacy"));
    QCOMPARE(restored.sessions().first().messages.size(), 1);
    QCOMPARE(restored.sessions().first().messages.first().content,
             QString("old message"));

    // The next save() migrates it in place: same file name, sealed content.
    restored.save("alice", configPath);
    QFile resealed(historyPath);
    QVERIFY(resealed.open(QIODevice::ReadOnly));
    const QByteArray raw = resealed.readAll();
    resealed.close();
    verifySealed(raw, "old message");
}

void TestChatSessionManager::unreadableHistoryYieldsNoSessions()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString configPath = dir.path() + "/settings.ini";
    const QString historyPath = dir.path() + "/chat_sessions_alice.json";

    // A blob that cannot be unsealed (other user/machine): the store must
    // report an empty history instead of surfacing garbage.
    QFile foreign(historyPath);
    QVERIFY(foreign.open(QIODevice::WriteOnly));
    foreign.write("enc1:bm90LWEtdmFsaWQtYmxvYg==");
    foreign.close();

    ChatSessionManager restored;
    restored.load("alice", configPath);
    QVERIFY(restored.isEmpty());
    QVERIFY(restored.currentId().isEmpty());
    QVERIFY(restored.currentMessages().isEmpty());
}

void TestChatSessionManager::missingHistoryYieldsNoSessions()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString configPath = dir.path() + "/settings.ini";

    // No file at all: an empty history, no crash.
    ChatSessionManager restored;
    restored.load("alice", configPath);
    QVERIFY(restored.isEmpty());
    QVERIFY(restored.currentId().isEmpty());
    QVERIFY(restored.currentMessages().isEmpty());
}

void TestChatSessionManager::aFailedSealKeepsThePreviousHistory()
{
#ifdef Q_OS_WIN
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString configPath = dir.path() + "/settings.ini";
    const QString historyPath = dir.path() + "/chat_sessions_alice.json";

    {
        ChatSessionManager sessions;
        ChatSession &session = sessions.create("Kept");
        ChatMessage message;
        message.role = ChatRole::User;
        message.content = QStringLiteral("first-history");
        session.messages.append(message);
        sessions.save("alice", configPath);
    }
    QFile before(historyPath);
    QVERIFY(before.open(QIODevice::ReadOnly));
    const QByteArray original = before.readAll();
    before.close();
    QVERIFY(!original.isEmpty());

    // The vault cannot protect the payload (CryptProtectData failed). It
    // returns an empty string; writing that would truncate the history, so the
    // store must keep the file it already has.
    SecretVault::setProtectionFailureForTesting(true);
    {
        ChatSessionManager sessions;
        ChatSession &session = sessions.create("Replacement");
        ChatMessage message;
        message.role = ChatRole::User;
        message.content = QStringLiteral("second-history");
        session.messages.append(message);
        sessions.save("alice", configPath);
    }
    SecretVault::setProtectionFailureForTesting(false);

    QFile after(historyPath);
    QVERIFY(after.open(QIODevice::ReadOnly));
    const QByteArray kept = after.readAll();
    after.close();
    QCOMPARE(kept, original);

    // The retained file is still the sealed first history, not a truncated
    // blob: the store did not write a partial payload either.
    ChatSessionManager restored;
    restored.load("alice", configPath);
    QCOMPARE(restored.sessions().size(), 1);
    QCOMPARE(restored.sessions().first().title, QString("Kept"));
#else
    QSKIP("fail-closed sealing only applies on a platform with an OS protection backend");
#endif
}

void TestChatSessionManager::anUnwritableHistoryPathKeepsThePreviousHistory()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString configPath = dir.path() + "/settings.ini";
    const QString historyPath = dir.path() + "/chat_sessions_alice.json";

    // A directory where the file is expected: QSaveFile cannot replace it, so
    // save() reports the failure instead of writing somewhere else or
    // destroying what is there.
    QVERIFY(QDir().mkpath(historyPath));

    ChatSessionManager sessions;
    sessions.create("Doomed");
    sessions.save("alice", configPath);

    QVERIFY(QFileInfo(historyPath).isDir());
    QVERIFY(QDir(historyPath).entryList(QDir::NoDotAndDotDot | QDir::AllEntries).isEmpty());
}

void TestChatSessionManager::corruptHistoryYieldsNoSessions()
{
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString configPath = dir.path() + "/settings.ini";
    const QString historyPath = dir.path() + "/chat_sessions_alice.json";

    // A file that is neither a sealed blob nor a session list.
    QFile corrupt(historyPath);
    QVERIFY(corrupt.open(QIODevice::WriteOnly));
    corrupt.write("this is not a session list");
    corrupt.close();

    ChatSessionManager restored;
    restored.load("alice", configPath);
    QVERIFY(restored.isEmpty());
    QVERIFY(restored.currentId().isEmpty());
    QVERIFY(restored.currentMessages().isEmpty());
}

// The typed message keeps the wire format byte-compatible (an existing
// sealed history still loads) and makes the invalid state — a UI-only role in
// the model input — inexpressible.
void TestChatSessionManager::theTypedMessageRoundTripsThroughTheWireFormat()
{
    ChatMessage message;
    message.role = ChatRole::User;
    message.content = QStringLiteral("hello");
    message.timestamp = QStringLiteral("12:00");
    message.attachments = {QStringLiteral("C:/a.png")};

    const QJsonObject wire = message.toJson();
    // The wire format is the one the sealed history already uses.
    QCOMPARE(wire.value("role").toString(), QStringLiteral("user"));
    QCOMPARE(wire.value("content").toString(), QStringLiteral("hello"));
    QCOMPARE(wire.value("timestamp").toString(), QStringLiteral("12:00"));
    QCOMPARE(wire.value("attachments").toArray().size(), 1);

    const ChatMessage back = ChatMessage::fromJson(wire);
    QCOMPARE(back.role, ChatRole::User);
    QCOMPARE(back.content, QStringLiteral("hello"));
    QCOMPARE(back.timestamp, QStringLiteral("12:00"));
    QCOMPARE(back.attachments, message.attachments);

    // The agent trace is a role of its own, and its envelope is parsed.
    ChatMessage trace;
    trace.role = ChatRole::AgentTrace;
    trace.content = QStringLiteral("{\"steps\":[],\"prior_step_count\":2}");
    const ChatMessage traceBack = ChatMessage::fromJson(trace.toJson());
    QCOMPARE(traceBack.role, ChatRole::AgentTrace);
    QCOMPARE(traceBack.agentTrace().value("prior_step_count").toInt(), 2);
}

void TestChatSessionManager::theAgentTraceIsNeverAModelRole()
{
    // The UI-only agent trace must never reach the model: sending it was the
    // source of HTTP 422 after changing mode. isModelRole() is the single
    // decision — and the type makes the invalid state impossible to write
    // down in the first place.
    ChatMessage user;
    user.role = ChatRole::User;
    QVERIFY(user.isModelRole());

    ChatMessage assistant;
    assistant.role = ChatRole::Assistant;
    QVERIFY(assistant.isModelRole());

    ChatMessage system;
    system.role = ChatRole::System;
    QVERIFY(system.isModelRole());

    ChatMessage trace;
    trace.role = ChatRole::AgentTrace;
    QVERIFY(!trace.isModelRole());
}

QTEST_MAIN(TestChatSessionManager)
#include "TestChatSessionManager.moc"
