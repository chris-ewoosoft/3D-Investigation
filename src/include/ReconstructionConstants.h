#ifndef RECONSTRUCTION_CONSTANTS_H
#define RECONSTRUCTION_CONSTANTS_H
#include <QString>
namespace AppConstants { namespace Reconstruction {
    constexpr int    MIN_POINTS_FOR_PNP        = 15;
    constexpr double PNP_REPROJECTION_ERROR_PX = 4.0;
    constexpr int    SIFT_MAX_FEATURES              = 20000;
    constexpr int    SIFT_OCTAVE_LAYERS             = 3;
    constexpr double SIFT_CONTRAST_THRESHOLD        = 0.01;
    constexpr double SIFT_EDGE_THRESHOLD            = 10.0;
    constexpr double SIFT_SIGMA                     = 1.6;
    constexpr float  MATCH_RATIO_THRESHOLD          = 0.8f;
    constexpr int    MIN_MATCHES_FOR_POSE           = 30;
    constexpr int    MIN_INLIERS_FOR_POSE           = 12;
    constexpr int    MIN_INLIERS_FOR_ESTIMATED_POSE = 30;
    constexpr int    SEARCH_WINDOW_SIZE             = 15;
    constexpr double DEPTH_LIMIT_MULTIPLIER         = 30.0;
    constexpr float  DEPTH_MIN_MEDIAN_RATIO         = 0.05f;
    constexpr float  DEPTH_MAX_MEDIAN_RATIO         = 20.0f;
    constexpr double REPROJECTION_ERROR_THRESHOLD   = 2.0;
} }
#endif
