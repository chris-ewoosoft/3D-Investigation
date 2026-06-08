#include "FileUtilities.h"
#include <QDir>
#include <QFileInfo>
#include <QDateTime>
#include <QPixmap>
#include <QPainter>
#include <QDebug>

FileUtilities::AttachmentResult FileUtilities::processAttachment(const QString &filePath, bool isImage, const QString &projectRoot) {
    AttachmentResult result;
    result.success = false;

    QString uploadDir = projectRoot + "/Upload/" + (isImage ? "Images" : "Files");
    QString thumbDir = projectRoot + "/Thumbnails/" + (isImage ? "Images" : "Files");
    
    QDir().mkpath(uploadDir);
    QDir().mkpath(thumbDir);

    QFileInfo fi(filePath);
    QString uniqueName = QString::number(QDateTime::currentMSecsSinceEpoch()) + "_" + fi.fileName();
    QString destPath = uploadDir + "/" + uniqueName;
    QString thumbPath = thumbDir + "/" + uniqueName + (isImage ? "" : ".png");

    if (!QFile::copy(filePath, destPath)) {
        return result;
    }

    result.destPath = destPath;
    result.thumbPath = thumbPath;

    if (isImage) {
        QPixmap original(destPath);
        result.thumbnail = original.scaled(80, 80, Qt::KeepAspectRatioByExpanding, Qt::SmoothTransformation);
        result.thumbnail.save(thumbPath);
    } else {
        result.thumbnail = QPixmap(80, 80);
        result.thumbnail.fill(QColor("#2a2a35"));
        QPainter p(&result.thumbnail);
        p.setPen(QColor("#ffffff"));
        p.setFont(QFont("Arial", 10, QFont::Bold));
        p.drawText(result.thumbnail.rect(), Qt::AlignCenter, fi.suffix().toUpper() + "\nFILE");
        result.thumbnail.save(thumbPath);
    }

    result.success = true;
    return result;
}

bool FileUtilities::deleteAttachment(const QString &filePath) {
    return QFile::remove(filePath);
}

QString FileUtilities::getUniqueFileName(const QString &originalName) {
    QFileInfo fi(originalName);
    return QString::number(QDateTime::currentMSecsSinceEpoch()) + "_" + fi.fileName();
}
