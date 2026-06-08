#pragma once
#include "Global.h"
#include <QColor>
#include <QIcon>
#include <QString>

/**
 * @brief Centralized factory for creating gradient-background emoji icons.
 *
 * Replaces the duplicated `static QIcon createModernIcon(...)` that existed
 * independently in every plugin's .cpp file.
 */
class APP_EXPORT IconFactory {
public:
    /**
     * Creates a 48×48 icon with a vertical gradient background and an emoji/text label.
     * @param emojiText  Emoji or short text rendered at the icon center.
     * @param startColor Top gradient color.
     * @param endColor   Bottom gradient color.
     */
    static QIcon createModern(const QString &emojiText,
                              const QColor  &startColor,
                              const QColor  &endColor);
};
