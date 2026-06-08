#include "FeatureMatcher.h"

void FeatureMatcher::match(const cv::Mat &desc1, const cv::Mat &desc2,
                           const ReconstructionConfig &config,
                           std::vector<cv::DMatch> &goodMatches)
{
    goodMatches.clear();
    if (desc1.empty() || desc2.empty()) return;

    cv::Mat d1 = desc1, d2 = desc2;
    if (d1.type() != CV_32F) d1.convertTo(d1, CV_32F);
    if (d2.type() != CV_32F) d2.convertTo(d2, CV_32F);

    cv::FlannBasedMatcher matcher;
    std::vector<std::vector<cv::DMatch>> knn12;
    matcher.knnMatch(d1, d2, knn12, 2);

    for (size_t i = 0; i < knn12.size(); ++i) {
        if (knn12[i].size() == 2 &&
            knn12[i][0].distance < config.matching.ratioThreshold * knn12[i][1].distance)
        {
            goodMatches.push_back(knn12[i][0]);
        }
    }

    if (config.matching.crossCheck) {
        std::vector<std::vector<cv::DMatch>> knn21;
        matcher.knnMatch(d2, d1, knn21, 2);
        std::vector<cv::DMatch> crossMatches;
        for (const auto &m12 : goodMatches) {
            int j = m12.trainIdx;
            if (knn21[j].size() == 2 && knn21[j][0].trainIdx == m12.queryIdx)
                crossMatches.push_back(m12);
        }
        goodMatches = crossMatches;
    }
}
