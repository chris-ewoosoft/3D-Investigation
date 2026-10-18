#ifndef POINTCLOUDDATA_H
#define POINTCLOUDDATA_H

#include <vector>

/**
 * @brief ABI-neutral point cloud data — no OpenCV dependency.
 * Used by IReconstructionService and ISceneService so plugin consumers
 * never include opencv2/core.hpp.
 */
struct Point3D {
    float x = 0.0f;
    float y = 0.0f;
    float z = 0.0f;
};

struct PointColor {
    unsigned char r = 0;
    unsigned char g = 0;
    unsigned char b = 0;
};

#endif // POINTCLOUDDATA_H
