#include "ChatMessage.h"

#include <QJsonArray>
#include <QJsonDocument>

QString ChatMessage::roleName() const
{
    switch (role) {
    case ChatRole::User:      return QStringLiteral("user");
    case ChatRole::Assistant: return QStringLiteral("assistant");
    case ChatRole::System:    return QStringLiteral("system");
    case ChatRole::AgentTrace:return QStringLiteral("assistant_agent");
    }
    return QStringLiteral("user");
}

QJsonObject ChatMessage::agentTrace() const
{
    if (role != ChatRole::AgentTrace || content.isEmpty())
        return {};
    return QJsonDocument::fromJson(content.toUtf8()).object();
}

QJsonObject ChatMessage::toJson() const
{
    QJsonObject object;
    object["role"] = roleName();
    object["content"] = content;
    if (!timestamp.isEmpty())
        object["timestamp"] = timestamp;
    if (!attachments.isEmpty()) {
        QJsonArray values;
        for (const QString &attachment : attachments)
            values.append(attachment);
        object["attachments"] = values;
    }
    return object;
}

ChatMessage ChatMessage::fromJson(const QJsonObject &object)
{
    ChatMessage message;
    const QString role = object.value("role").toString();
    if (role == QStringLiteral("assistant"))
        message.role = ChatRole::Assistant;
    else if (role == QStringLiteral("system"))
        message.role = ChatRole::System;
    else if (role == QStringLiteral("assistant_agent"))
        message.role = ChatRole::AgentTrace;
    else
        message.role = ChatRole::User;

    message.content = object.value("content").toString();
    message.timestamp = object.value("timestamp").toString();

    const QJsonArray values = object.value("attachments").toArray();
    for (const QJsonValue &value : values) {
        const QString attachment = value.toString();
        if (!attachment.isEmpty())
            message.attachments.append(attachment);
    }
    return message;
}
