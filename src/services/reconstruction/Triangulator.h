#pragma once
#include <opencv2/core.hpp>
#include <vector>

class Triangulator {
public:
    // Triangulate 3D points from two projection matrices and corresponding 2D points.
    static void triangulate(const cv::Mat &P0, const cv::Mat &P1,
                            const std::vector<cv::Point2f> &pts0,
                            const std::vector<cv::Point2f> &pts1,
                            std::vector<cv::Point3f> &outPts);
};
