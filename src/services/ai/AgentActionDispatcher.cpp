#include "AgentActionDispatcher.h"

#include <QJsonArray>

#include "AgentActionManifest.h"

AgentActionDispatcher::AgentActionDispatcher(IAppPaths *paths, QObject *parent)
    : QObject(parent), m_paths(paths) {
}

QPair<QString, int> AgentActionDispatcher::takePending(const QString &requestId) {
    return m_pending.take(requestId);
}

void AgentActionDispatcher::clearPending() {
    m_pending.clear();
}

void AgentActionDispatcher::dispatch(const QJsonObject &response, bool isUiActionAck,
                                     const QString &ackRequestId, const QString &sessionId,
                                     int insertAfterIndex) {
    const QString responseRequestId = response.value("request_id").toString();
    // The response names the ONE action it parked. Without that id the steps
    // array is only history: an earlier application_action would be replayed.
    if (responseRequestId.isEmpty()) return;

    if (isUiActionAck) {
        // A plain ACK response continues the action we just reported, and its
        // history contains that action. Only a NEW parked action (different
        // request id) is a follow-up the desktop must execute.
        const bool isFollowUp =
            response.value("status").toString() == QLatin1String("pending_ui_action") &&
            responseRequestId != ackRequestId;
        if (!isFollowUp) return;
    }

    const QJsonArray steps = response.value("steps").toArray();
    for (const QJsonValue &value : steps) {
        const QJsonObject step = value.toObject();
        if (step.value("type").toString() != QLatin1String("tool_call") ||
            step.value("tool").toString() != QLatin1String("application_action")) {
            continue;
        }
        const QJsonObject params = step.value("params").toObject();
        if (params.value("request_id").toString() != responseRequestId) continue;

        QString action = params.value("action").toString();
        if (action.isEmpty()) continue;

        // The agent response itself is inserted at insertAfterIndex; the ACK
        // belongs right after it, not at the end of the conversation.
        const int nextInsertIndex = (insertAfterIndex >= 0) ? insertAfterIndex + 1 : -1;
        m_pending.insert(responseRequestId, {sessionId, nextInsertIndex});

        QString manifestError;
        QVariantMap actionParams = params.toVariantMap();
        if (!AgentActionManifest::canonicalize(action, actionParams, &manifestError, m_paths)) {
            // The pending entry stays: the rejection handler reports the ACK
            // through reportUiActionResult(), which consumes it.
            emit uiActionRejected(responseRequestId, manifestError);
            break;
        }
        // Agents send actions without file inputs; fill the demo fixtures from
        // Config/agent_action_manifest.json so plugins never embed sample paths.
        AgentActionManifest::applySampleDefaults(action, actionParams, m_paths);
        emit uiActionRequested(action, actionParams);
        // One response names one action: a duplicated step with the same
        // request id must not run the side effect twice.
        break;
    }
}
