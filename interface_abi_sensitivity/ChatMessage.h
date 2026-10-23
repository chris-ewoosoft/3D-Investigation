#ifndef CHATMESSAGE_H
#define CHATMESSAGE_H

#include <QJsonObject>
#include <QString>
#include <QStringList>

#include "Global.h"

/**
 * @brief The role of a chat message — a CLOSED set.
 *
 * The model accepts exactly three roles; the fourth is UI-only state (the
 * agent step trace). Before this type existed the role was a free-form string
 * inside a QJsonObject, and a UI-only role once reached FastAPI and came back
 * as HTTP 422 — a mistake the type system now makes impossible.
 */
enum class ChatRole {
    User,      ///< "user"       — sent to the model
    Assistant, ///< "assistant"  — sent to the model
    System,    ///< "system"     — sent to the model
    AgentTrace ///< "assistant_agent" — UI-only agent steps, NEVER sent
};

/**
 * @brief One chat message.
 *
 * A typed value instead of a QJsonObject with magic string keys: the role is
 * a closed enum, the attachments are a list of paths, and an invalid state
 * (a UI-only role in the model input) cannot be expressed.
 *
 * The WIRE and DISK format is unchanged: toJson()/fromJson() map to and from
 * exactly the object shape the sealed chat history already uses, so an
 * existing history still loads and the Python service still receives the same
 * payload.
 */
struct APP_EXPORT ChatMessage {
    ChatRole role = ChatRole::User;
    QString content;      ///< the text; for AgentTrace the raw envelope JSON
    QStringList attachments; ///< file paths
    QString timestamp;    ///< display timestamp (already formatted)

    /// Whether the model accepts this role. The agent trace is UI-only.
    bool isModelRole() const
    {
        return role == ChatRole::User || role == ChatRole::Assistant
               || role == ChatRole::System;
    }

    /// The role as the wire format spells it.
    QString roleName() const;

    /// The parsed agent-step envelope of an AgentTrace message ({steps,
    /// prior_step_count}); empty for every other role and for a content that
    /// is not a JSON object.
    QJsonObject agentTrace() const;

    /// The wire/disk format — byte-compatible with the sealed chat history.
    QJsonObject toJson() const;

    /// Reads the wire/disk format. An unknown role is read as User, and an
    /// unreadable attachment list is read as empty (never as a crash).
    static ChatMessage fromJson(const QJsonObject &object);
};

#endif // CHATMESSAGE_H
