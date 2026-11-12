#ifndef AIMODE_H
#define AIMODE_H

/**
 * @brief Interaction mode of the 2D viewer.
 *
 * Type-safe replacement for the previous QString constants: a typo can no
 * longer silently select a nonexistent mode. The mode is never persisted or
 * logged as text, so no enum→string mapping is provided.
 */
enum class AIMode {
    None,
    Detection,
    Segmentation,
    Tracking,
};

#endif // AIMODE_H
