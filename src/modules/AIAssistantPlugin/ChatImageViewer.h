#ifndef CHATIMAGEVIEWER_H
#define CHATIMAGEVIEWER_H

#include <QDialog>
#include <QLabel>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QPushButton>
#include <QPixmap>
#include <QScreen>
#include <QEvent>

// Plugin-local UI widget (AIAssistantPlugin). Previously lived in AppCore
// services/viewer, which forced a concrete-class dependency from the plugin
// onto the service layer; it is chat UI, so it belongs to the chat plugin.
class ChatImageViewer : public QDialog {
    Q_OBJECT
public:
    ChatImageViewer(const QString &imagePath, QWidget *parent = nullptr);

protected:
    bool eventFilter(QObject *obj, QEvent *event) override;
};

#endif // CHATIMAGEVIEWER_H
