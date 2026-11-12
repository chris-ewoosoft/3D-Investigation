#ifndef OVERLAY_RESIZER_H
#define OVERLAY_RESIZER_H

#include <QObject>

class QWidget;
class QLabel;

/**
 * @brief Keeps the background overlay in sync with the shell window.
 *
 * A named class (not a function-local one) on purpose: the instance lives in
 * the shell's widget tree and its vtable lives in this plugin's DLL, so the
 * owner has to be able to find it again and remove it before the DLL is
 * unloaded. A function-local class could only be created, never removed.
 */
class OverlayResizer : public QObject {
    Q_OBJECT
public:
    explicit OverlayResizer(QObject *parent) : QObject(parent) {}
    bool eventFilter(QObject *obj, QEvent *event) override;

    /**
     * @brief Apply (or remove) the background image on @p window.
     *
     * @param filter the caller's filter slot. It is CREATED when the
     *        background becomes active and RELEASED when it is switched off
     *        or when the background cannot be applied; it is never replaced
     *        by a second instance. The caller owns it: this function only
     *        fills in the slot and removes the filter from the window.
     *
     * The slot IS the state: there is no second source of truth (a widget
     * property, a "is installed" flag) that could drift away from the
     * filter that is actually installed on the window.
     */
    static void applyTo(QWidget *window, const QString &imagePath, int opacity,
                        bool enabled, OverlayResizer *&filter);
};

#endif // OVERLAY_RESIZER_H
