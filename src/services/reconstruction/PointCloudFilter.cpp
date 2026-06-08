#include "PointCloudFilter.h"
#include <QDebug>
#include <algorithm>
#include <cmath>
#include <vector>

#include <pcl/filters/radius_outlier_removal.h>
#include <pcl/filters/statistical_outlier_removal.h>
#include <pcl/filters/voxel_grid.h>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>

typedef pcl::PointXYZRGB PointT;
typedef pcl::PointCloud<PointT> PointCloudT;

// ─── helpers ────────────────────────────────────────────────────────────────

static pcl::PointCloud<pcl::PointXYZ>::Ptr buildXYZCloud(
    const std::vector<cv::Point3f> &pts)
{
    auto cloud = pcl::make_shared<pcl::PointCloud<pcl::PointXYZ>>();
    cloud->resize(pts.size());
    for (size_t i = 0; i < pts.size(); ++i) {
        cloud->points[i].x = pts[i].x;
        cloud->points[i].y = pts[i].y;
        cloud->points[i].z = pts[i].z;
    }
    cloud->width  = (uint32_t)pts.size();
    cloud->height = 1;
    cloud->is_dense = false;
    return cloud;
}

static void applyIndices(const std::vector<int> &idx,
                         std::vector<cv::Point3f> &pts,
                         std::vector<cv::Vec3b>   &cols)
{
    std::vector<cv::Point3f> np;
    std::vector<cv::Vec3b>   nc;
    np.reserve(idx.size());
    nc.reserve(idx.size());
    for (int i : idx) { np.push_back(pts[i]); nc.push_back(cols[i]); }
    pts.swap(np);
    cols.swap(nc);
}

// ─── public methods ─────────────────────────────────────────────────────────

void PointCloudFilter::statisticalOutlier(std::vector<cv::Point3f> &pts,
                                           std::vector<cv::Vec3b>   &cols,
                                           float meanK,
                                           float stdDevMulThresh)
{
    if (pts.empty()) return;
    int k = std::max(1, (int)meanK);
    if ((int)pts.size() <= k) { qDebug() << "SOR: too few points, skipping."; return; }
    size_t n = pts.size();
    auto cloud = buildXYZCloud(pts);
    pcl::StatisticalOutlierRemoval<pcl::PointXYZ> sor;
    sor.setInputCloud(cloud);
    sor.setMeanK(k);
    sor.setStddevMulThresh(stdDevMulThresh);
    std::vector<int> kept;
    sor.filter(kept);
    applyIndices(kept, pts, cols);
    qDebug() << "SOR: kept" << pts.size() << "/" << n;
}

void PointCloudFilter::radiusOutlier(std::vector<cv::Point3f> &pts,
                                      std::vector<cv::Vec3b>   &cols,
                                      float radius,
                                      int   minNeighbors)
{
    if (pts.empty() || radius <= 0.f || minNeighbors < 1) return;
    size_t n = pts.size();
    auto cloud = buildXYZCloud(pts);
    pcl::RadiusOutlierRemoval<pcl::PointXYZ> ror;
    ror.setInputCloud(cloud);
    ror.setRadiusSearch(radius);
    ror.setMinNeighborsInRadius(minNeighbors);
    std::vector<int> kept;
    ror.filter(kept);
    applyIndices(kept, pts, cols);
    qDebug() << "ROR: kept" << pts.size() << "/" << n;
}

void PointCloudFilter::voxelGrid(std::vector<cv::Point3f> &pts,
                                  std::vector<cv::Vec3b>   &cols,
                                  float leafSize)
{
    if (pts.empty() || leafSize <= 0.f) return;
    size_t n = pts.size();

    // Compute bbox to avoid PCL integer overflow
    float xmin = pts[0].x, xmax = xmin;
    float ymin = pts[0].y, ymax = ymin;
    float zmin = pts[0].z, zmax = zmin;
    for (const auto &p : pts) {
        xmin = std::min(xmin, p.x); xmax = std::max(xmax, p.x);
        ymin = std::min(ymin, p.y); ymax = std::max(ymax, p.y);
        zmin = std::min(zmin, p.z); zmax = std::max(zmax, p.z);
    }
    float range = std::max({xmax - xmin, ymax - ymin, zmax - zmin});
    float leaf  = std::max(leafSize, range / 2048.0f);
    if (leaf != leafSize)
        qDebug() << "VoxelGrid: leaf adjusted to" << leaf << "to prevent overflow.";

    PointCloudT::Ptr cloud(new PointCloudT);
    cloud->resize(n);
    for (size_t i = 0; i < n; ++i) {
        auto &p = cloud->points[i];
        p.x = pts[i].x; p.y = pts[i].y; p.z = pts[i].z;
        p.b = cols[i][0]; p.g = cols[i][1]; p.r = cols[i][2];
    }
    cloud->width = (uint32_t)n; cloud->height = 1; cloud->is_dense = false;

    pcl::VoxelGrid<PointT> vg;
    vg.setInputCloud(cloud);
    vg.setLeafSize(leaf, leaf, leaf);
    PointCloudT::Ptr filtered(new PointCloudT);
    vg.filter(*filtered);

    pts.clear(); cols.clear();
    pts.reserve(filtered->size()); cols.reserve(filtered->size());
    for (const auto &p : filtered->points) {
        pts.push_back(cv::Point3f(p.x, p.y, p.z));
        cols.push_back(cv::Vec3b(p.b, p.g, p.r));
    }
    qDebug() << "VoxelGrid: kept" << pts.size() << "/" << n;
}

void PointCloudFilter::densityFilter(std::vector<cv::Point3f> &pts,
                                      std::vector<cv::Vec3b>   &cols,
                                      float radius, int minNeighbors)
{
    if (pts.empty()) return;
    size_t n = pts.size();
    std::vector<bool> keep(n, false);
    for (size_t i = 0; i < n; ++i) {
        int cnt = 0;
        for (size_t j = 0; j < n; ++j) {
            if (i == j) continue;
            float dx = pts[i].x - pts[j].x;
            float dy = pts[i].y - pts[j].y;
            float dz = pts[i].z - pts[j].z;
            if (std::sqrt(dx*dx + dy*dy + dz*dz) < radius && ++cnt >= minNeighbors) break;
        }
        if (cnt >= minNeighbors) keep[i] = true;
    }
    std::vector<cv::Point3f> np; std::vector<cv::Vec3b> nc;
    for (size_t i = 0; i < n; ++i)
        if (keep[i]) { np.push_back(pts[i]); nc.push_back(cols[i]); }
    qDebug() << "DensityFilter: kept" << np.size() << "/" << n;
    pts.swap(np); cols.swap(nc);
}
