#ifndef AVATARCROPPERDIALOG_H
#define AVATARCROPPERDIALOG_H

#include "../../utils/ModernDialog.h"

#include <QImage>

class QListWidget;
class QPushButton;

class AvatarCropperDialog : public ModernDialog {
    Q_OBJECT
public:
    explicit AvatarCropperDialog(const QString& username, QWidget *parent = nullptr);
    QPixmap getCroppedAvatar() const;
    void accept() override;

protected:
    void paintEvent(QPaintEvent *event) override;
    bool eventFilter(QObject *obj, QEvent *event) override;
    void mousePressEvent(QMouseEvent *event) override;
    void mouseMoveEvent(QMouseEvent *event) override;
    void mouseReleaseEvent(QMouseEvent *event) override;
    void wheelEvent(QWheelEvent *event) override;

private slots:
    void onUpload();
    void onAvatarSelected();
    void onDeleteAvatar();

private:
    void loadHistory();
    void loadImage(const QString &path);

    QString m_username;
    QImage m_sourceImage;
    QString m_currentImagePath;
    double m_scale = 1.0;
    QPointF m_offset;
    QPoint m_lastMousePos;
    bool m_isDragging = false;
    int m_cropSize = 256;

    QListWidget *m_historyList;
    QPushButton *m_uploadBtn;
    QWidget    *m_canvas = nullptr;
};

#endif // AVATARCROPPERDIALOG_H
