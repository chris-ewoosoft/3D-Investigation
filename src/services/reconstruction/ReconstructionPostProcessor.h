#ifndef RECONSTRUCTIONPOSTPROCESSOR_H
#define RECONSTRUCTIONPOSTPROCESSOR_H

#include <opencv2/core.hpp>
#include <QString>
#include <vector>

#include <pcl/PolygonMesh.h>

#include "ReconstructionConfig.h"

/**
 * @brief Point-cloud / mesh post-processing stage of the reconstruction
 *        pipeline: cleanup filters, KNN densification, Poisson meshing and
 *        best-view texturing.
 *
 * Stateless (all state lives in the vectors passed in) so every step is
 * independently testable and ReconstructionPipeline stays an orchestrator.
 * Output color is BGR (cv::Vec3b) throughout, matching cv::Mat images.
 */
class ReconstructionPostProcessor {
public:
    /**
     * Cleanup profile for track-based (ground-truth) clouds: adaptive ROR →
     * SOR → VoxelGrid, tuned by the *Track fields of @p filter.
     */
    static void processPointCloud(const FilterConfig &filter,
                                  std::vector<cv::Point3f> &points3D,
                                  std::vector<cv::Vec3b> &colors);

    /** Remove points beyond mean + sigma·stddev from the cloud centroid. */
    static void filterFarOutliers(std::vector<cv::Point3f> &points3D,
                                  std::vector<cv::Vec3b> &colors,
                                  float sigma);

    /**
     * Densify by interpolating random points along edges to KNN neighbours
     * (deterministic RNG seed). Grows @p points3D/@p colors in place up to
     * @p targetCount.
     */
    static void densifyPointCloudMLS(std::vector<cv::Point3f> &points3D,
                                     std::vector<cv::Vec3b> &colors,
                                     int targetCount);

    /** Poisson surface reconstruction + bounding-box trim of the "wings". */
    static pcl::PolygonMesh poissonMeshing(const std::vector<cv::Point3f> &densePts,
                                           const std::vector<cv::Vec3b> &denseCols,
                                           int poissonDepth);

    /** Project a world point with a 3x4 projection matrix (false if behind camera). */
    static bool projectWithP(const cv::Point3f &pw, const CameraParams &cam,
                             cv::Point2f &uv);

    /** Re-color a point cloud by the best-view (image-centre-biased) sample. */
    static void textureFromImages(std::vector<cv::Point3f> &pts,
                                  std::vector<cv::Vec3b> &cols,
                                  const std::vector<cv::Mat> &images,
                                  const std::vector<CameraParams> &cams);

    /**
     * Re-color mesh vertices by best-view sampling and save the result.
     * @p outputPath empty → no file is written.
     */
    static void textureMeshFromImages(pcl::PolygonMesh &mesh,
                                      const std::vector<cv::Mat> &images,
                                      const std::vector<CameraParams> &cams,
                                      const QString &outputPath);
};

#endif // RECONSTRUCTIONPOSTPROCESSOR_H
