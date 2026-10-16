#pragma once
#include <opencv2/core.hpp>
#include <opencv2/features2d.hpp>
#include <vector>
#include "ReconstructionConfig.h"

class FeatureMatcher {
public:
    // Performs kNN matching with Lowe ratio test + optional cross-check
    static void match(const cv::Mat &desc1, const cv::Mat &desc2,
                      const ReconstructionConfig &config,
                      std::vector<cv::DMatch> &goodMatches);
};
