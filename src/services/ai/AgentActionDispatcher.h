#ifndef AGENTACTIONDISPATCHER_H
#define AGENTACTIONDISPATCHER_H

#include <QJsonObject>
#include <QMap>
#include <QObject>
#include <QPair>
#include <QString>
#include <QVariantMap>

#include "Global.h"

class IAppPaths;

/**
 * @brief Turns the UI-action steps of an agent response into desktop actions.
 *
 * The Python agent returns the full step history on every response, so a
 * response may contain `application_action` steps the desktop already executed
 * in an earlier request. Dispatching those again replays file loads, AI runs
 * and other side effects. This class is the ONE place that decides what may
 * run:
 *
 *  - a response with no top-level `request_id` dispatches nothing (the steps
 *    array is history, not a command list);
 *  - a step runs only when its `params.request_id` equals that top-level id;
 *  - a UI-action ACK response never dispatches, unless the ACK parked a NEW
 *    `pending_ui_action` with a different request id (a chained workflow);
 *  - a step that fails the manifest contract is rejected, not dispatched.
 *
 * It also owns the `requestId → (session, insert index)` bookkeeping used to
 * insert the follow-up ACK at the right place in the conversation instead of
 * appending it to the end.
 */
class APP_EXPORT AgentActionDispatcher : public QObject {
    Q_OBJECT
public:
    explicit AgentActionDispatcher(IAppPaths *paths, QObject *parent = nullptr);

    /// Examines one agent response and emits at most one UI action per matching
    /// request id. `ackRequestId` is the request id this response ACKs (empty
    /// when the response follows a fresh execute request).
    void dispatch(const QJsonObject &response, bool isUiActionAck,
                  const QString &ackRequestId, const QString &sessionId,
                  int insertAfterIndex = -1);

    /// Removes and returns the pending entry for a request id. The pair is
    /// `(sessionId, insertAfterIndex)`; both are empty/-1 when unknown.
    QPair<QString, int> takePending(const QString &requestId);

    /// Drops every pending entry — the backend generation they belong to is
    /// gone, so a stale ACK must not be routed into a restarted server.
    void clearPending();

    int pendingCount() const { return m_pending.size(); }

signals:
    void uiActionRequested(const QString &action, const QVariantMap &parameters);
    void uiActionRejected(const QString &requestId, const QString &error);

private:
    IAppPaths *m_paths = nullptr;
    QMap<QString, QPair<QString, int>> m_pending;
};

#endif // AGENTACTIONDISPATCHER_H
