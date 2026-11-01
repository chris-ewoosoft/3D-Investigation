#include "AIAssistantPayloadBuilder.h"

#include <QJsonArray>

#include "AppConstants.h"

QJsonArray AIAssistantPayloadBuilder::textArray(const QStringList &values) {
    QJsonArray array;
    for (const QString &value : values) array.append(value);
    return array;
}

QJsonObject AIAssistantPayloadBuilder::buildCompletionPayload(
    const QList<ChatMessage> &messages, double temperature, const QString &language) {
    QJsonArray array;
    for (const ChatMessage &message : messages) {
        // `assistant_agent` is UI-only state and is not a valid chat role.
        // isModelRole() is the single decision about what the model may see:
        // sending the trace used to be the source of HTTP 422 after a mode
        // switch.
        if (!message.isModelRole()) continue;
        array.append(message.toJson());
    }

    QJsonObject payload;
    payload["messages"] = array;
    payload["temperature"] = temperature;
    payload["max_tokens"] = AppConstants::AIServer::DEFAULT_MAX_TOKENS;
    payload["language"] = language;
    return payload;
}

QJsonObject AIAssistantPayloadBuilder::buildAgentPayload(
    const QList<ChatMessage> &messages, const QString &task,
    const QStringList &attachments, double temperature, const QString &language) {
    QJsonArray history;
    // The current user message is passed as ``task``. Keep only the portable
    // conversational roles so Agent step JSON never contaminates model input.
    for (int index = 0; index + 1 < messages.size(); ++index) {
        const ChatMessage &message = messages.at(index);
        if (message.role != ChatRole::User && message.role != ChatRole::Assistant) continue;
        QJsonObject item;
        item["role"] = message.roleName();
        item["content"] = message.content;
        if (!message.attachments.isEmpty()) item["attachments"] = textArray(message.attachments);
        history.append(item);
    }

    QJsonObject payload;
    payload["task"] = task;
    payload["history"] = history;
    payload["temperature"] = temperature;
    payload["language"] = language;
    if (!attachments.isEmpty()) payload["attachments"] = textArray(attachments);
    return payload;
}

QString AIAssistantPayloadBuilder::completionContent(const QJsonObject &response) {
    const QJsonArray choices = response.value("choices").toArray();
    if (choices.isEmpty()) return QString();
    return choices.first().toObject().value("message").toObject()
        .value("content").toString();
}
