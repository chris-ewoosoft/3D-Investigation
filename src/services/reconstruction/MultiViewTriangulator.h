#pragma once
#include <opencv2/core.hpp>
#include <vector>
#include "FeatureTrack.h"

class MultiViewTriangulator {
public:
    // Triangulate 1 track bằng DLT-SVD trên TẤT CẢ view quan sát nó
    static bool triangulateTrack(FeatureTrack &track,
                                 const std::vector<cv::Mat> &projMatrices,   // P của mỗi ảnh
                                 const std::vector<std::vector<cv::KeyPoint>> &keypoints,
                                 double maxReprojError = 2.0);

    // Point-only refinement: tối ưu vị trí điểm 3D bằng Gauss-Newton,
    // pose GIỮ NGUYÊN (đã là ground truth) — rẻ hơn Bundle Adjustment đầy đủ
    static void refinePoint(FeatureTrack &track,
                            const std::vector<cv::Mat> &projMatrices,
                            const std::vector<std::vector<cv::KeyPoint>> &keypoints,
                            int iterations = 5);

    static bool validateTrack(FeatureTrack &track,
                              const std::vector<cv::Mat> &projMatrices,
                              const std::vector<std::vector<cv::KeyPoint>> &keypoints,
                              double maxReprojError = 2.0);
};
