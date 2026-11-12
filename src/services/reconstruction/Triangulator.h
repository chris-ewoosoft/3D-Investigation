#ifndef TRIANGULATOR_H
#define TRIANGULATOR_H
#include <opencv2/core.hpp>
#include <vector>

class Triangulator {
public:
    // Triangulate 3D points from two projection matrices and corresponding 2D points.
    //
    // @p outPts is index-aligned with @p pts0/@p pts1 — a correspondence that
    // cannot be triangulated (its homogeneous w is ~0: the point is at
    // infinity) yields a NaN point instead of being dropped, so callers can
    // keep pairing index i with the i-th match. Skip non-finite points.
    static void triangulate(const cv::Mat &P0, const cv::Mat &P1,
                            const std::vector<cv::Point2f> &pts0,
                            const std::vector<cv::Point2f> &pts1,
                            std::vector<cv::Point3f> &outPts);
};

#endif // TRIANGULATOR_H
