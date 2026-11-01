#ifndef CHAT_SESSION_STORE_H
#define CHAT_SESSION_STORE_H

#include "IAIAssistantService.h"

namespace ChatSessionStore {

// The store is intentionally path-injected (configFilePath comes from
// IAppPaths/AppConfig at the call site) so it never touches a global
// singleton itself.
void save(const QList<ChatSession> &sessions, const QString &username,
          const QString &configFilePath);
QList<ChatSession> load(const QString &username, const QString &configFilePath);
QString generateSessionId();
QString sessionsPath(const QString &username, const QString &configFilePath);

}

#endif // CHAT_SESSION_STORE_H
