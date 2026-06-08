#pragma once
#include <opencv2/core.hpp>
#include <vector>

class PoseEstimator {
public:
    // Estimate relative pose (R, t) from matched 2D point pairs using Essential Matrix.
    // Returns false if not enough inliers.
    static bool estimatePose(const std::vector<cv::Point2f> &pts1,
                             const std::vector<cv::Point2f> &pts2,
                             const cv::Mat &K,
                             cv::Mat &R, cv::Mat &t,
                             int minInliers);

    // Compute reprojection error of a single 3D→2D projection
    static double reprojectionError(const cv::Mat &P,
                                    const cv::Point3f &pt3d,
                                    const cv::Point2f &pt2d);

    // Estimate intrinsics K from image dimensions
    static cv::Mat estimateIntrinsics(const cv::Mat &img);
};
