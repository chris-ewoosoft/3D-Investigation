#ifndef FEATUREEXTRACTOR_H
#define FEATUREEXTRACTOR_H
#include <opencv2/core.hpp>
#include <opencv2/features2d.hpp>
#include <vector>
#include "ReconstructionConfig.h"

class FeatureExtractor {
public:
    static void extract(const cv::Mat &img, const ReconstructionConfig &config,
                        std::vector<cv::KeyPoint> &keypoints, cv::Mat &descriptors);
};

#endif // FEATUREEXTRACTOR_H
