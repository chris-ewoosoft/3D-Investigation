#ifndef FEATUREMATCHER_H
#define FEATUREMATCHER_H
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

    /**
     * RANSAC-filter matches against the fundamental matrix.
     * Returns the inlier subset; when the fit is not credible (< 8 inliers)
     * or ransacThreshold <= 0, the input set is returned unchanged (or an
     * empty vector when the estimation itself failed).
     */
    static std::vector<cv::DMatch> filterByFundamental(
        const std::vector<cv::DMatch> &matches,
        const std::vector<cv::KeyPoint> &keypoints1,
        const std::vector<cv::KeyPoint> &keypoints2,
        double ransacThreshold);
};

#endif // FEATUREMATCHER_H
