#include "ChatSessionStore.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
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
            messages.append(message);
        }
        sessionObject["messages"] = messages;
        sessionArray.append(sessionObject);
    }

    QFile file(sessionsPath(username, configFilePath));
    if (file.open(QIODevice::WriteOnly)) {
        file.write(QJsonDocument(sessionArray).toJson());
        file.close();
    }
}

QList<ChatSession> load(const QString &username, const QString &configFilePath)
{
    QList<ChatSession> sessions;
    QFile file(sessionsPath(username, configFilePath));
    if (!file.open(QIODevice::ReadOnly)) return sessions;

    const QJsonArray sessionArray = QJsonDocument::fromJson(file.readAll()).array();
    file.close();

    for (const auto &value : sessionArray) {
        const QJsonObject sessionObject = value.toObject();
        ChatSession session;
        session.id = sessionObject["id"].toString();
        session.title = sessionObject["title"].toString();
        session.createdAt = QDateTime::fromString(sessionObject["createdAt"].toString(), Qt::ISODate);
        session.titleIsDefault = sessionObject["titleIsDefault"].toBool();
        for (const auto &message : sessionObject["messages"].toArray()) {
            session.messages.append(message.toObject());
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
