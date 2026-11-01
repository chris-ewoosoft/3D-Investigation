#include "AIAdminClient.h"

#include "AppConstants.h"
#include "ILanguageService.h"

#include <QJsonDocument>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>

#include <utility>

namespace {
constexpr char kAdminAuthorizationHeader[] = "Authorization";
}

AIAdminClient::AIAdminClient(ILanguageService *language,
                             QNetworkAccessManager *network,
                             int serverPort, TokenProvider tokenProvider,
                             QObject *parent)
    : QObject(parent)
    , m_language(language)
    , m_network(network)
    , m_serverPort(serverPort)
    , m_tokenProvider(std::move(tokenProvider))
{
}

void AIAdminClient::restartModel(bool serverReady)
{
    reload("reload-model", "ai.restarting_model", "ai.restart_model_failed",
           "Model reloaded", 600000, serverReady);
}

void AIAdminClient::restartRag(bool serverReady)
{
    reload("reload-rag", "ai.restarting_rag", "ai.restart_rag_failed",
           "RAG reloaded", 600000, serverReady);
}

void AIAdminClient::restartAgent(bool serverReady)
{
    reload("reload-agent", "ai.restarting_agent", "ai.restart_agent_failed",
           "Agent reset", 15000, serverReady);
}

void AIAdminClient::reload(const char *endpoint, const char *startingKey,
                           const char *failureKey, const char *defaultMessage,
                           int timeoutMs, bool serverReady)
{
    if (!serverReady) {
        emit errorOccurred(m_language->translate("ai.server_not_running"));
        return;
    }

    emit statusChanged(m_language->translate(startingKey));
    QNetworkRequest request{
        QUrl(AppConstants::AIServer::adminEndpoint(QString::fromLatin1(endpoint), m_serverPort))};
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    request.setRawHeader(kAdminAuthorizationHeader,
                         QByteArrayLiteral("Bearer ") + m_tokenProvider().toUtf8());
    request.setTransferTimeout(timeoutMs);

    QNetworkReply *reply = m_network->post(request, QByteArrayLiteral("{}"));
    connect(reply, &QNetworkReply::finished, this,
            [this, reply, failureKey, defaultMessage]() {
                reply->deleteLater();
                if (reply->error() == QNetworkReply::NoError) {
                    const QJsonDocument document = QJsonDocument::fromJson(reply->readAll());
                    const QString fallback = QString::fromLatin1(defaultMessage);
                    emit statusChanged(QStringLiteral("✅ ") +
                                       document.object().value("message").toString(fallback));
                    return;
                }
                emit errorOccurred(m_language->translate(failureKey) + QStringLiteral(" ") +
                                   reply->errorString());
            });
}
