#ifndef CHATSESSIONMANAGER_H
#define CHATSESSIONMANAGER_H

#include <QList>
#include <QString>

#include "Global.h"
#include "IAIAssistantService.h" // ChatSession

/**
 * @brief Owns the chat-session collection and the current-session pointer.
 *
 * Extracted from AIAssistant so the session rules are unit-testable without a
 * network stack or process manager:
 *  - create() always makes the new session current,
 *  - remove() also deletes the session's attachment/thumbnail files and falls
 *    back to the last remaining session (no current session when none is left),
 *  - replaceAll() (persistence load) selects the last session as current.
 *
 * Persistence itself stays in ChatSessionStore; the manager only orchestrates
 * it with a username + config path so no global singleton is touched.
 */
class APP_EXPORT ChatSessionManager {
public:
    ChatSessionManager() = default;

    // ── Queries ──────────────────────────────────────────────────────────────
    const QList<ChatSession> &sessions() const { return m_sessions; }
    bool isEmpty() const { return m_sessions.isEmpty(); }
    QString currentId() const { return m_currentId; }

    /// Session with @p id, or nullptr. Any mutation invalidates the pointer.
    ChatSession *find(const QString &id);
    ChatSession *current();
    QList<QJsonObject> currentMessages() const;

    // ── Mutations ────────────────────────────────────────────────────────────
    /// Appends a new session (id + createdAt filled) and makes it current.
    ChatSession &create(const QString &title);
    /// Switches the current session; false when the id is unknown.
    bool setCurrent(const QString &id);
    /// Removes the session together with its attachment files and updates the
    /// current selection. Returns false when the id is unknown.
    bool remove(const QString &id);
    /// Replaces the whole list (persistence load); makes the last one current.
    void replaceAll(QList<ChatSession> sessions);

    // ── Persistence (delegates to ChatSessionStore) ──────────────────────────
    void load(const QString &username, const QString &configFilePath);
    void save(const QString &username, const QString &configFilePath) const;

private:
    void selectLastAsCurrent();

    QList<ChatSession> m_sessions;
    QString m_currentId;
};

#endif // CHATSESSIONMANAGER_H
