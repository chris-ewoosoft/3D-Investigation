#ifndef LOGGER_H
#define LOGGER_H

#include <QtGlobal>
#include <QString>
#include "Global.h"

namespace Logger {
    APP_EXPORT void initialize();
    APP_EXPORT void customMessageHandler(QtMsgType type, const QMessageLogContext &context, const QString &msg);
}

#endif // LOGGER_H
