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

#include "Global.h"

class APP_EXPORT ChatImageViewer : public QDialog {
    Q_OBJECT
public:
    ChatImageViewer(const QString &imagePath, QWidget *parent = nullptr);

protected:
    bool eventFilter(QObject *obj, QEvent *event) override;
};

#endif // CHATIMAGEVIEWER_H
