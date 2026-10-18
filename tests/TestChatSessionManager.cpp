#include <QtTest>
#include <QDir>
#include <QFile>
#include <QJsonArray>
#include <QJsonObject>
#include <QTemporaryDir>

#include "ChatSessionManager.h"

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
    void removeDeletesAttachmentsAndFallsBack();
    void removingLastSessionLeavesNoCurrent();
    void replaceAllSelectsLastSession();
    void saveAndLoadRoundTrip();

private:
    static bool writeFile(const QString &path) {
        QFile f(path);
        if (!f.open(QIODevice::WriteOnly)) return false;
        f.write("x");
        return true;
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
    current.messages.append(QJsonObject{
        {"role", "user"},
        {"attachments", QJsonArray{QString(image), QString(doc)}},
    });

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
        session.messages.append(QJsonObject{{"role", "user"}, {"content", "hello"}});
        sessions.save("alice", configPath);
    }

    QFile stored(dir.path() + "/chat_sessions_alice.json");
    QVERIFY(stored.exists());

    ChatSessionManager restored;
    restored.load("alice", configPath);
    QCOMPARE(restored.sessions().size(), 1);
    QCOMPARE(restored.sessions().first().title, QString("Round trip"));
    QCOMPARE(restored.currentId(), restored.sessions().first().id);
    const QList<QJsonObject> messages = restored.currentMessages();
    QCOMPARE(messages.size(), 1);
    QCOMPARE(messages.first().value("content").toString(), QString("hello"));

    // Unknown user → empty manager, no current session.
    ChatSessionManager other;
    other.load("bob", configPath);
    QVERIFY(other.isEmpty());
    QVERIFY(other.currentId().isEmpty());
}

QTEST_MAIN(TestChatSessionManager)
#include "TestChatSessionManager.moc"
