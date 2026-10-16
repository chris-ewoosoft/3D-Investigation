#include "IconFactory.h"
#include <QPainter>
#include <QPixmap>
#include <QFont>
#include <QLinearGradient>

QIcon IconFactory::createModern(const QString &emojiText,
                                 const QColor  &startColor,
                                 const QColor  &endColor)
{
    QPixmap pix(64, 64);
    pix.fill(Qt::transparent);
    QPainter painter(&pix);
    painter.setRenderHint(QPainter::Antialiasing);

    QLinearGradient grad(0, 0, 0, 64);
    grad.setColorAt(0.0, startColor);
    grad.setColorAt(1.0, endColor);
    painter.setBrush(grad);
    painter.setPen(Qt::NoPen);
    painter.drawRoundedRect(2, 2, 60, 60, 14, 14);

    QFont font = painter.font();
    font.setPixelSize(32);
    font.setBold(true);
    painter.setFont(font);
    painter.setPen(Qt::white);
    painter.drawText(QRect(2, 2, 60, 60), Qt::AlignCenter, emojiText);

    return QIcon(pix);
}
