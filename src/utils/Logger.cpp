#include "Logger.h"
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QTextStream>
#include <iostream>
#include <mutex>
#include "AppConfig.h"

#ifdef Q_OS_WIN
#include <windows.h>
#endif


namespace Logger {

static std::mutex s_logMutex;
static QFile s_logFile;
static QTextStream s_logStream;
static QString s_logsDir;
static QString s_cachedDate;

static void ensureFileOpenUnderLock() {
  const QString currentDate = QDateTime::currentDateTime().toString("yyyy-MM-dd");
  if (s_logFile.isOpen() && s_cachedDate == currentDate) {
    return;
  }

  if (s_logFile.isOpen()) {
    s_logStream.flush();
    s_logFile.close();
  }

  if (s_logsDir.isEmpty()) s_logsDir = AppConfig::bootstrap().logsDir();
  const QString logsDir = s_logsDir;
  QDir().mkpath(logsDir);
  const QString filePath = logsDir + "/" + currentDate + ".txt";

  s_logFile.setFileName(filePath);
  if (s_logFile.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
    s_logStream.setDevice(&s_logFile);
    s_cachedDate = currentDate;
  }
}

void customMessageHandler(QtMsgType type, const QMessageLogContext &context,
                          const QString &msg) {
  Q_UNUSED(context);
  if (type == QtWarningMsg && msg.contains("Unable to open monitor interface")) {
    return;
  }

  QString txt;
  const QString timestamp =
      QDateTime::currentDateTime().toString("yyyy-MM-dd HH:mm:ss");

  switch (type) {
  case QtDebugMsg:
    txt = QString("[%1] [DEBUG] %2").arg(timestamp, msg);
    break;
  case QtInfoMsg:
    txt = QString("[%1] [INFO] %2").arg(timestamp, msg);
    break;
  case QtWarningMsg:
    txt = QString("[%1] [WARNING] %2").arg(timestamp, msg);
    break;
  case QtCriticalMsg:
    txt = QString("[%1] [CRITICAL] %2").arg(timestamp, msg);
    break;
  case QtFatalMsg:
    txt = QString("[%1] [FATAL] %2").arg(timestamp, msg);
    break;
  }

  std::lock_guard<std::mutex> lock(s_logMutex);

  // Print to terminal (UTF-8 để hiển thị đúng tiếng Việt trong Qt Creator)
  std::cout << txt.toUtf8().constData() << std::endl;

  // Write to cached log file
  ensureFileOpenUnderLock();
  if (s_logFile.isOpen()) {
    s_logStream << txt << '\n';
    s_logStream.flush();
  }

  if (type == QtFatalMsg) {
    if (s_logFile.isOpen()) {
      s_logStream.flush();
      s_logFile.close();
    }
    abort();
  }
}

void setLogsDir(const QString& dir) {
    s_logsDir = dir;
}

void initialize() {
#ifdef Q_OS_WIN
  // Bật UTF-8 output cho console Windows (ảnh hưởng cả Qt Creator Application Output)
  SetConsoleOutputCP(CP_UTF8);
  SetConsoleCP(CP_UTF8);
#endif
  qInstallMessageHandler(customMessageHandler);
}

void shutdown() {
  std::lock_guard<std::mutex> lock(s_logMutex);
  qInstallMessageHandler(nullptr);
  if (s_logFile.isOpen()) {
    s_logStream.flush();
    s_logFile.close();
  }
  s_cachedDate.clear();
}

} // namespace Logger

