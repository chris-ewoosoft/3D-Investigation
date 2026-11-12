#ifndef AIASSISTANTPAYLOADBUILDER_H
#define AIASSISTANTPAYLOADBUILDER_H

#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QString>
#include <QStringList>

#include "ChatMessage.h"
#include "Global.h"

/**
 * @brief Builds the JSON bodies AIAssistant sends to the local AI server.
 *
 * Pure functions: no HTTP, no process, no UI state. The role filtering is the
 * load-bearing part — the UI-only agent trace must never reach the model, and
 * the agent request's history must exclude the task that is passed separately.
 * Keeping the wire shape here lets `AIAssistant` stay an orchestrator and
 * makes the payload contract testable without a network or a Python child.
 */
class APP_EXPORT AIAssistantPayloadBuilder final {
public:
    /// `/v1/chat/completions` body: every model-role message, the configured
    /// temperature, the token cap and the response language.
    static QJsonObject buildCompletionPayload(const QList<ChatMessage> &messages,
                                              double temperature,
                                              const QString &language);

    /// `/v1/agent/execute` body: the task, the portable conversational history
    /// (the last message is the task itself and is therefore skipped), the
    /// temperature/language and, when present, the attachments.
    static QJsonObject buildAgentPayload(const QList<ChatMessage> &messages,
                                         const QString &task,
                                         const QStringList &attachments,
                                         double temperature,
                                         const QString &language);

    /// Content of the first completion choice; empty when the envelope has no
    /// choice (an error body, a truncated response, …).
    static QString completionContent(const QJsonObject &response);

private:
    static QJsonArray textArray(const QStringList &values);
};

#endif // AIASSISTANTPAYLOADBUILDER_H
