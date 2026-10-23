#include "ChatSessionStore.h"

#include "SecretVault.h"

#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QSaveFile>
#include <QUuid>

namespace ChatSessionStore {

void save(const QList<ChatSession> &sessions, const QString &username,
          const QString &configFilePath)
{
    QJsonArray sessionArray;
    for (const auto &session : sessions) {
        QJsonObject sessionObject;
        sessionObject["id"] = session.id;
        sessionObject["title"] = session.title;
        sessionObject["createdAt"] = session.createdAt.toString(Qt::ISODate);
        sessionObject["titleIsDefault"] = session.titleIsDefault;

        QJsonArray messages;
        for (const auto &message : session.messages) {
            messages.append(message.toJson());
        }
        sessionObject["messages"] = messages;
        sessionArray.append(sessionObject);
    }

    // The chat history is sealed with the OS vault (DPAPI on Windows) before
    // it reaches the disk: the file must never hold readable content.
    const QByteArray payload = QJsonDocument(sessionArray).toJson();
    const QString sealed = SecretVault::encrypt(QString::fromUtf8(payload));

    // Fail closed on the *write* as well: the vault returns an empty string
    // when the platform cannot protect the payload (no OS backend) and on
    // Windows when CryptProtectData fails. Writing that empty string would
    // truncate the existing history and lose it — the opposite of what the
    // vault's contract asks for. The previous file is kept instead.
    if (sealed.isEmpty()) {
        qWarning() << "ChatSessionStore: the vault could not seal the chat "
                      "history; the previous history was kept unchanged.";
        return;
    }

    const QByteArray blob = sealed.toUtf8();
    // QSaveFile writes to a temporary file next to the target and renames it
    // into place on commit(). That is the property this write needs: a crash,
    // a full disk or a short write leaves the *previous* history untouched
    // instead of a truncated one. Plain QFile + Truncate destroyed the old
    // file before the new payload was known to be intact.
    QSaveFile file(sessionsPath(username, configFilePath));
    const bool written = file.open(QIODevice::WriteOnly)
                         && file.write(blob) == blob.size()
                         && file.commit();
    if (!written) {
        // commit() already discarded the temporary file; the history on disk
        // is the one from the previous successful save.
        qWarning() << "ChatSessionStore: the chat history could not be written "
                      "in full; the previous history was kept unchanged.";
    }
}

QList<ChatSession> load(const QString &username, const QString &configFilePath)
{
    QList<ChatSession> sessions;
    QFile file(sessionsPath(username, configFilePath));
    if (!file.open(QIODevice::ReadOnly)) return sessions;

    const QByteArray raw = file.readAll();
    file.close();

    // The payload is sealed with the OS vault. A history written before the
    // vault existed is plain JSON and passes through decrypt() unchanged, so
    // it still loads and is re-sealed by the next save().
    const QString plain = SecretVault::decrypt(QString::fromUtf8(raw));
    // An empty answer is the vault's "cannot be unsealed" signal (a blob
    // from another user/machine): report no sessions instead of parsing
    // garbage. An empty file is simply empty.
    if (plain.isEmpty()) {
        if (!raw.isEmpty())
            qWarning() << "ChatSessionStore: chat history could not be decrypted.";
        return sessions;
    }

    QJsonParseError parseError{};
    const QJsonDocument document = QJsonDocument::fromJson(plain.toUtf8(), &parseError);
    if (parseError.error != QJsonParseError::NoError || !document.isArray()) {
        qWarning() << "ChatSessionStore: chat history is not a valid session list.";
        return sessions;
    }

    const QJsonArray sessionArray = document.array();
    for (const auto &value : sessionArray) {
        const QJsonObject sessionObject = value.toObject();
        ChatSession session;
        session.id = sessionObject["id"].toString();
        session.title = sessionObject["title"].toString();
        session.createdAt = QDateTime::fromString(sessionObject["createdAt"].toString(), Qt::ISODate);
        session.titleIsDefault = sessionObject["titleIsDefault"].toBool();
        for (const auto &message : sessionObject["messages"].toArray()) {
            // The typed message is built from the wire format — the file on
            // disk keeps exactly the shape it had before the DTO existed.
            session.messages.append(ChatMessage::fromJson(message.toObject()));
        }
        sessions.append(session);
    }

    return sessions;
}

QString generateSessionId()
{
    return QUuid::createUuid().toString(QUuid::WithoutBraces);
}

QString sessionsPath(const QString &username, const QString &configFilePath)
{
    QString currentUser = username;
    if (currentUser.isEmpty()) currentUser = "default";

    const QString dir = QFileInfo(configFilePath).absolutePath() + "/";
    QDir().mkpath(dir);
    return dir + "chat_sessions_" + currentUser + ".json";
}

}
