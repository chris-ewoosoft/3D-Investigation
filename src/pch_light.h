#ifndef PCH_LIGHT_H
#define PCH_LIGHT_H

// ─── Light precompiled header ────────────────────────────────────────────────
// For plugins (and future light targets) that do NOT manipulate VTK/OpenCV/
// PCL types: keeps their translation units free of the heavy SDK headers.
// Keep this file Qt/STL-only — heavy additions belong to src/pch.h, which
// only the heavy targets (exe, AppCore, Viewer/Reconstruction-adjacent
// plugins) compile.

// ─── Qt Core ─────────────────────────────────────────────────────────────────
#include <QAction>
#include <QApplication>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QList>
#include <QMap>
#include <QMenu>
#include <QObject>
#include <QPointer>
#include <QPushButton>
#include <QString>
#include <QStringList>
#include <QTimer>
#include <QWidget>

// ─── Qt Widgets (frequently used across plugins) ─────────────────────────────
#include <QMainWindow>
#include <QBoxLayout>
#include <QHBoxLayout>
#include <QVBoxLayout>
#include <QLabel>
#include <QStackedWidget>
#include <QDockWidget>
#include <QFrame>
#include <QSplitter>
#include <QToolButton>
#include <QComboBox>
#include <QLineEdit>
#include <QTextEdit>
#include <QScrollArea>
#include <QGroupBox>

// ─── Qt Network / JSON (used by AI, Mail, etc.) ─────────────────────────────
#include <QJsonObject>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonValue>

// ─── Qt GUI ──────────────────────────────────────────────────────────────────
#include <QPixmap>
#include <QIcon>
#include <QFont>
#include <QColor>
#include <QPainter>
#include <QMouseEvent>

// ─── STL ─────────────────────────────────────────────────────────────────────
#include <algorithm>
#include <atomic>
#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

#endif // PCH_LIGHT_H
