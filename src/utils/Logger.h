#ifndef LOGGER_H
#define LOGGER_H

#include <QtGlobal>
#include <QString>

namespace Logger {
    void initialize();
    void customMessageHandler(QtMsgType type, const QMessageLogContext &context, const QString &msg);
}

#endif // LOGGER_H
