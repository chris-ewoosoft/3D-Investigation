#include "PoseEstimator.h"
#include <opencv2/calib3d.hpp>
#include <cmath>

bool PoseEstimator::estimatePose(const std::vector<cv::Point2f> &pts1,
                                  const std::vector<cv::Point2f> &pts2,
                                  const cv::Mat &K,
                                  cv::Mat &R, cv::Mat &t,
                                  int minInliers)
{
    if ((int)pts1.size() < 8) return false;
    cv::Mat mask;
    cv::Mat E = cv::findEssentialMat(pts1, pts2, K, cv::RANSAC, 0.999, 1.0, mask);
    if (E.empty()) return false;
    int inliers = cv::recoverPose(E, pts1, pts2, K, R, t, mask);
    return (inliers >= minInliers);
}

double PoseEstimator::reprojectionError(const cv::Mat &P,
                                         const cv::Point3f &pt3d,
                                         const cv::Point2f &pt2d)
{
    cv::Mat p4 = (cv::Mat_<double>(4,1) << pt3d.x, pt3d.y, pt3d.z, 1.0);
    cv::Mat proj = P * p4;
    double iw = 1.0 / proj.at<double>(2);
    double u = proj.at<double>(0) * iw;
    double v = proj.at<double>(1) * iw;
    return std::sqrt((u - pt2d.x) * (u - pt2d.x) + (v - pt2d.y) * (v - pt2d.y));
}

cv::Mat PoseEstimator::estimateIntrinsics(const cv::Mat &img)
{
    double w = img.cols, h = img.rows;
    double focal = std::max(w, h) * 1.2;
    return (cv::Mat_<double>(3, 3) << focal, 0.0, w / 2.0,
            0.0, focal, h / 2.0,
            0.0, 0.0, 1.0);
}
