#include "ChatSessionManager.h"

#include "ChatSessionStore.h"

#include <QDateTime>
#include <QFile>
#include <QJsonArray>
#include <QJsonObject>

ChatSession *ChatSessionManager::find(const QString &id)
{
    for (auto &session : m_sessions) {
        if (session.id == id) return &session;
    }
    return nullptr;
}

ChatSession *ChatSessionManager::current()
{
    return find(m_currentId);
}

QList<QJsonObject> ChatSessionManager::currentMessages() const
{
    for (const auto &session : m_sessions) {
        if (session.id == m_currentId) return session.messages;
    }
    return {};
}

ChatSession &ChatSessionManager::create(const QString &title)
{
    ChatSession session;
    session.id        = ChatSessionStore::generateSessionId();
    session.title     = title;
    session.createdAt = QDateTime::currentDateTime();
    m_sessions.append(session);
    m_currentId = session.id;
    return m_sessions.last();
}

bool ChatSessionManager::setCurrent(const QString &id)
{
    if (!find(id)) return false;
    m_currentId = id;
    return true;
}

bool ChatSessionManager::remove(const QString &id)
{
    for (int i = 0; i < m_sessions.size(); ++i) {
        if (m_sessions[i].id != id) continue;

        // Capture before removeAt(): `id` may alias the string inside the very
        // element that is about to be destroyed.
        const bool wasCurrent = (m_currentId == id);

        // Physical cleanup of the session's attachments (upload + thumbnail).
        for (const QJsonObject &message : m_sessions[i].messages) {
            for (const QJsonValue &value : message.value("attachments").toArray()) {
                const QString path = value.toString();
                QFile::remove(path);
                QString thumbPath = path;
                thumbPath.replace("/Upload/", "/Thumbnails/");
                if (!path.endsWith(".png") && !path.endsWith(".jpg") && !path.endsWith(".jpeg")) {
                    thumbPath += ".png";
                }
                QFile::remove(thumbPath);
            }
        }

        m_sessions.removeAt(i);
        if (wasCurrent) selectLastAsCurrent();
        return true;
    }
    return false;
}

void ChatSessionManager::replaceAll(QList<ChatSession> sessions)
{
    m_sessions = std::move(sessions);
    selectLastAsCurrent();
}

void ChatSessionManager::load(const QString &username, const QString &configFilePath)
{
    replaceAll(ChatSessionStore::load(username, configFilePath));
}

void ChatSessionManager::save(const QString &username, const QString &configFilePath) const
{
    ChatSessionStore::save(m_sessions, username, configFilePath);
}

void ChatSessionManager::selectLastAsCurrent()
{
    m_currentId = m_sessions.isEmpty() ? QString() : m_sessions.last().id;
}
