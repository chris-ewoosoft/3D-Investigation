#ifndef RECONSTRUCTION_CONFIG_H
#define RECONSTRUCTION_CONFIG_H

#include <opencv2/core.hpp>
#include <QString>

struct CameraParams {
    QString imageName;
    cv::Mat K; // 3x3
    cv::Mat R; // 3x3
    cv::Mat t; // 3x1
    cv::Mat P; // 3x4 = K * [R|t]
};

struct SiftConfig {
    int nfeatures = 20000;
    int nOctaveLayers = 3;
    double contrastThreshold = 0.01;
    double edgeThreshold = 10.0;
    double sigma = 1.6;
};

struct MatchingConfig {
    float ratioThreshold = 0.8f;
    bool crossCheck = true;
};

struct FilterConfig {
    // Statistical Outlier Removal
    int sorMeanK = 50;
    float sorStdDevMul = 0.8f; // More aggressive

    // Radius Outlier Removal
    float rorRadius = 0.005f; // Much smaller, proportional to voxel size
    int rorMinNeighbors = 6;  // More neighbors required

    // Voxel Grid
    float voxelLeafSize = 0.0005f;
};

struct ReconstructionConfig {
    SiftConfig sift;
    MatchingConfig matching;
    FilterConfig filter;
    
    // Global limits
    double reprojectionErrorMax = 1.2;
    int minMatches = 20;
    int searchWindow = 15;
};

#endif // RECONSTRUCTION_CONFIG_H
