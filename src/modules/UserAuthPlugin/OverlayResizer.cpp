#include "OverlayResizer.h"

#include "StyleManager.h"

#include <QColor>
#include <QEvent>
#include <QLabel>
#include <QPainter>
#include <QPixmap>
#include <QWidget>

namespace {
/// The object name of the overlay label installed on the shell window.
const char *kOverlayObjectName = "bgGlobalOverlay";
} // namespace

bool OverlayResizer::eventFilter(QObject *obj, QEvent *event)
{
    if (event->type() == QEvent::Resize) {
        QWidget* w = qobject_cast<QWidget*>(obj);
        if (w) {
            QLabel* overlay = w->findChild<QLabel*>(QString::fromLatin1(kOverlayObjectName));
            if (overlay) {
                overlay->setGeometry(w->rect());
                overlay->lower(); // Keep it at the bottom
            }
        }
    }
    return QObject::eventFilter(obj, event);
}

void OverlayResizer::applyTo(QWidget *window, const QString &imagePath, int opacity,
                              bool enabled, OverlayResizer *&filter)
{
    if (!window) {
        // No window to decorate: release whatever the caller had.
        if (filter) {
            if (QObject *host = filter->parent()) host->removeEventFilter(filter);
            delete filter;
            filter = nullptr;
        }
        return;
    }

    if (!enabled || imagePath.isEmpty()) {
        // Background disabled: the overlay goes away and so does the filter.
        // Leaving the filter behind would keep the shell calling into this
        // DLL for a window that has no overlay any more.
        if (QLabel *oldOverlay = window->findChild<QLabel*>(QString::fromLatin1(kOverlayObjectName)))
            oldOverlay->deleteLater();
        if (filter) {
            if (QObject *host = filter->parent()) host->removeEventFilter(filter);
            delete filter;
            filter = nullptr;
        }
        return;
    }

    QLabel *overlay = window->findChild<QLabel*>(QString::fromLatin1(kOverlayObjectName));
    if (!overlay) {
        overlay = new QLabel(window);
        overlay->setObjectName(QString::fromLatin1(kOverlayObjectName));
        // Must be transparent for mouse events so user can click through
        overlay->setAttribute(Qt::WA_TransparentForMouseEvents);
        overlay->setScaledContents(true);
        // Ensure it stays on top
        overlay->raise();
        overlay->show();
    }

    // The filter follows the overlay's existence: it is created together with
    // the overlay and released together with it.
    if (!filter) {
        filter = new OverlayResizer(window);
        window->installEventFilter(filter);
    }

    // Ensure overlay is correctly sized immediately and on bottom
    overlay->setGeometry(window->rect());
    overlay->lower();

    QPixmap pm(imagePath);
    if (!pm.isNull()) {
        // Apply color tint blend manually to the pixmap
        QPixmap tinted = pm;
        QPainter p(&tinted);
        QColor accent(StyleManager::accentColor());
        double alpha = opacity / 100.0;
        accent.setAlpha(qRound(alpha * 255));
        p.fillRect(tinted.rect(), accent);
        p.end();

        overlay->setPixmap(tinted);
        overlay->show();
    } else {
        overlay->hide();
    }
}
