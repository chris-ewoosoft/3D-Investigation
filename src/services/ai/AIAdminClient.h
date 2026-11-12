#ifndef AI_ADMIN_CLIENT_H
#define AI_ADMIN_CLIENT_H

#include "Global.h"

#include <QObject>
#include <QString>

#include <functional>

class ILanguageService;
class QNetworkAccessManager;

/**
 * Owns the HTTP protocol for the AI server's administrative reload endpoints.
 * AIAssistant remains the public facade, but no longer duplicates request,
 * timeout and response handling for model, RAG and agent reloads.
 */
class APP_EXPORT AIAdminClient final : public QObject {
    Q_OBJECT
public:
    using TokenProvider = std::function<QString()>;

    AIAdminClient(ILanguageService *language, QNetworkAccessManager *network,
                  int serverPort, TokenProvider tokenProvider,
                  QObject *parent = nullptr);

    void restartModel(bool serverReady);
    void restartRag(bool serverReady);
    void restartAgent(bool serverReady);

signals:
    void statusChanged(const QString &status);
    void errorOccurred(const QString &error);

private:
    void reload(const char *endpoint, const char *startingKey,
                const char *failureKey, const char *defaultMessage,
                int timeoutMs, bool serverReady);

    ILanguageService *m_language = nullptr;
    QNetworkAccessManager *m_network = nullptr;
    int m_serverPort = 0;
    TokenProvider m_tokenProvider;
};

#endif // AI_ADMIN_CLIENT_H
