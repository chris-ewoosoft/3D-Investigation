#include "ReconstructionPostProcessor.h"
#include "PointCloudFilter.h"

#include <QDebug>

#include <pcl/common/centroid.h>
#include <pcl/common/io.h>
#include <pcl/conversions.h>
#include <pcl/features/normal_3d_omp.h>
#include <pcl/io/ply_io.h>
#include <pcl/kdtree/kdtree_flann.h>
#include <pcl/point_types.h>
#include <pcl/search/kdtree.h>
#include <pcl/surface/poisson.h>

#include <algorithm>
#include <cmath>
#include <memory>
#include <random>

// ─── processPointCloud ───────────────────────────────────────────────────────

void ReconstructionPostProcessor::processPointCloud(const FilterConfig &f,
                                                    std::vector<cv::Point3f> &points3D,
                                                    std::vector<cv::Vec3b> &colors)
{
    if (points3D.empty()) return;
    qDebug() << "Post-processing. Initial points:" << points3D.size();

    // A value of 0 means "this filter is switched off" — the configuration
    // defaults are all non-zero, so 0 is unambiguous. Replacing it with a
    // default here would make the user's "auto-clean off" preference a
    // no-op: the filters would run anyway.
    //
    // B1: Adaptive ROR trước - loại bụi cô lập, giữ cột mỏng
    // Log 11662 của bạn: radius = 0.00433567, kept 13838/14819
    const float mult = f.rorRadiusMultiplierTrack > 0 ? f.rorRadiusMultiplierTrack : 6.0f;
    if (f.rorMinNeighborsTrack > 0) {
        PointCloudFilter::adaptiveRadiusOutlier(points3D, colors, mult, f.rorMinNeighborsTrack);
        if (points3D.empty()) { qWarning() << "No points after adaptive ROR."; return; }
    }

    // B2: SOR - loại điểm bay lơ lửng, để 1.2f là cân bằng nhất
    if (f.sorMeanKTrack > 0) {
        const float sorStd = f.sorStdDevMulTrack > 0 ? f.sorStdDevMulTrack : 1.2f;
        PointCloudFilter::statisticalOutlier(points3D, colors, f.sorMeanKTrack, sorStd);
        if (points3D.empty()) { qWarning() << "No points after SOR."; return; }
    }

    // B3: VoxelGrid - giữ chi tiết, không *0.5 nữa
    float leaf = f.voxelLeafSizeTrack > 0 ? f.voxelLeafSizeTrack : 0.003f;
    PointCloudFilter::voxelGrid(points3D, colors, leaf);

    qDebug() << "After post-processing:" << points3D.size() << "points";
}

void ReconstructionPostProcessor::filterFarOutliers(std::vector<cv::Point3f> &points3D,
                                                    std::vector<cv::Vec3b> &colors,
                                                    float sigma)
{
    if (points3D.empty()) return;
    cv::Point3f centroid(0,0,0);
    for (auto &p: points3D){ centroid.x+=p.x; centroid.y+=p.y; centroid.z+=p.z; }
    centroid.x/=points3D.size(); centroid.y/=points3D.size(); centroid.z/=points3D.size();

    std::vector<float> dists; dists.reserve(points3D.size());
    for (auto &p: points3D){
        float dx=p.x-centroid.x, dy=p.y-centroid.y, dz=p.z-centroid.z;
        dists.push_back(sqrtf(dx*dx+dy*dy+dz*dz));
    }
    float mean=0; for(auto d:dists) mean+=d; mean/=dists.size();
    float var=0; for(auto d:dists) var+=(d-mean)*(d-mean); var/=dists.size();
    float stddev = sqrtf(var);
    float thresh = mean + sigma*stddev; // sigma=2.5 là vừa cho templeRing

    std::vector<cv::Point3f> p2; p2.reserve(points3D.size());
    std::vector<cv::Vec3b> c2; c2.reserve(colors.size());
    for(size_t i=0;i<points3D.size();++i){
        if(dists[i] <= thresh){ p2.push_back(points3D[i]); c2.push_back(colors[i]); }
    }
    qDebug() << "Far outlier filter:" << points3D.size() << "->" << p2.size()
             << " thresh" << thresh << " mean" << mean;
    points3D.swap(p2); colors.swap(c2);
}

void ReconstructionPostProcessor::densifyPointCloudMLS(
    std::vector<cv::Point3f> &points3D,
    std::vector<cv::Vec3b> &colors,
    int targetCount)
{
    if (points3D.empty()) return;
    const std::vector<cv::Point3f> &pts = points3D;
    const std::vector<cv::Vec3b> &cols = colors;
    qDebug() << "MVS Densify (KNN Interpolate). Input:" << pts.size() << "target:" << targetCount;

    // Build KdTree cho cloud gốc
    auto cloud = std::make_shared<pcl::PointCloud<pcl::PointXYZ>>();
    cloud->resize(pts.size());
    for (size_t i=0;i<pts.size();++i) (*cloud)[i] = pcl::PointXYZ(pts[i].x, pts[i].y, pts[i].z);

    pcl::KdTreeFLANN<pcl::PointXYZ> kdtree;
    kdtree.setInputCloud(cloud);

    int factor = std::max(1, targetCount / (int)pts.size()); // 2500000/11725 = 213
    std::vector<cv::Point3f> outPts; outPts.reserve(targetCount);
    std::vector<cv::Vec3b> outCols; outCols.reserve(targetCount);

    std::mt19937 rng(42);
    std::uniform_real_distribution<float> dist(0.0f, 1.0f);
    std::normal_distribution<float> noise(0.0f, 0.0005f); // nhiễu 0.5mm

    std::vector<int> indices(6);
    std::vector<float> sqDist(6);

    for (size_t i=0;i<pts.size();++i){
        outPts.push_back(pts[i]);
        outCols.push_back(cols[i]);

        if (kdtree.nearestKSearch((*cloud)[i], 6, indices, sqDist) < 2) continue;

        for (int f=0; f < factor-1; ++f){
            int j = indices[1 + (rng() % 5)]; // lấy ngẫu nhiên 1 trong 5 hàng xóm gần
            float t = dist(rng);
            cv::Point3f p;
            p.x = pts[i].x * (1-t) + pts[j].x * t + noise(rng);
            p.y = pts[i].y * (1-t) + pts[j].y * t + noise(rng);
            p.z = pts[i].z * (1-t) + pts[j].z * t + noise(rng);

            // nội suy màu
            cv::Vec3b c;
            c[0] = (uchar)(cols[i][0]*(1-t) + cols[j][0]*t);
            c[1] = (uchar)(cols[i][1]*(1-t) + cols[j][1]*t);
            c[2] = (uchar)(cols[i][2]*(1-t) + cols[j][2]*t);

            outPts.push_back(p);
            outCols.push_back(c);
            if ((int)outPts.size() >= targetCount) break;
        }
        if ((int)outPts.size() >= targetCount) break;
    }

    qDebug() << "MVS Densify done:" << outPts.size();
    points3D = outPts;
    colors = outCols;
}

pcl::PolygonMesh ReconstructionPostProcessor::poissonMeshing(
    const std::vector<cv::Point3f>& densePts,
    const std::vector<cv::Vec3b>& denseCols,
    int poissonDepth)
{
    qDebug() << "Poisson Input:" << densePts.size() << "depth:" << poissonDepth;

    // Tính bounding box của point cloud gốc để lát cắt vòm
    cv::Point3f minPt(1e9,1e9,1e9), maxPt(-1e9,-1e9,-1e9);
    for(auto &p: densePts){
        minPt.x = std::min(minPt.x, p.x); minPt.y = std::min(minPt.y, p.y); minPt.z = std::min(minPt.z, p.z);
        maxPt.x = std::max(maxPt.x, p.x); maxPt.y = std::max(maxPt.y, p.y); maxPt.z = std::max(maxPt.z, p.z);
    }
    float diag = sqrtf((maxPt.x-minPt.x)*(maxPt.x-minPt.x) + (maxPt.y-minPt.y)*(maxPt.y-minPt.y) + (maxPt.z-minPt.z)*(maxPt.z-minPt.z));
    float expand = diag * 0.08f; // cho phép nở 8%
    minPt.x-=expand; minPt.y-=expand; minPt.z-=expand;
    maxPt.x+=expand; maxPt.y+=expand; maxPt.z+=expand;

    pcl::PointCloud<pcl::PointXYZRGB>::Ptr cloud(new pcl::PointCloud<pcl::PointXYZRGB>);
    cloud->resize(densePts.size());
    for (size_t i=0;i<densePts.size();++i){
        (*cloud)[i].x = densePts[i].x; (*cloud)[i].y = densePts[i].y; (*cloud)[i].z = densePts[i].z;
        (*cloud)[i].r = denseCols[i][2]; (*cloud)[i].g = denseCols[i][1]; (*cloud)[i].b = denseCols[i][0];
    }

    pcl::PointCloud<pcl::PointNormal>::Ptr cloudNormals(new pcl::PointCloud<pcl::PointNormal>);
    pcl::NormalEstimationOMP<pcl::PointXYZRGB, pcl::PointNormal> ne;
    ne.setInputCloud(cloud);
    ne.setKSearch(30);
    pcl::search::KdTree<pcl::PointXYZRGB>::Ptr tree(new pcl::search::KdTree<pcl::PointXYZRGB>);
    ne.setSearchMethod(tree);
    ne.compute(*cloudNormals);
    pcl::copyPointCloud(*cloud, *cloudNormals);

    // Orient normal về centroid của cloud, không về 0,0,0
    Eigen::Vector4f centroid;
    pcl::compute3DCentroid(*cloud, centroid);
    for (auto &p : cloudNormals->points){
        Eigen::Vector3f n(p.normal_x, p.normal_y, p.normal_z);
        Eigen::Vector3f vp(centroid[0]-p.x, centroid[1]-p.y, centroid[2]-p.z);
        if (n.dot(vp) < 0){ p.normal_x*=-1; p.normal_y*=-1; p.normal_z*=-1; }
    }

    pcl::Poisson<pcl::PointNormal> poisson;
    poisson.setDepth(poissonDepth);
    poisson.setInputCloud(cloudNormals);
    poisson.setIsoDivide(8);
    pcl::PolygonMesh mesh;
    poisson.reconstruct(mesh);

    // --- TRIM: cắt hết đỉnh Poisson nằm ngoài bounding box gốc ---
    pcl::PointCloud<pcl::PointXYZRGB> meshCloud;
    pcl::fromPCLPointCloud2(mesh.cloud, meshCloud);
    pcl::PointCloud<pcl::PointXYZRGB> trimmed;
    // old index -> new index; -1 for a vertex that was removed.
    std::vector<int> remap(meshCloud.size(), -1);
    for (size_t i = 0; i < meshCloud.points.size(); ++i) {
        const auto &pt = meshCloud.points[i];
        if (pt.x>=minPt.x && pt.x<=maxPt.x && pt.y>=minPt.y && pt.y<=maxPt.y && pt.z>=minPt.z && pt.z<=maxPt.z) {
            remap[i] = static_cast<int>(trimmed.size());
            trimmed.push_back(pt);
        }
    }
    qDebug() << "Poisson trimmed:" << meshCloud.size() << "->" << trimmed.size() << " (removed wing)";

    // The faces reference the vertices by index: they have to be remapped
    // together with the vertices, and a face that lost a vertex has to be
    // dropped. Replacing the vertices alone leaves faces pointing at
    // vertices that no longer exist — a corrupt mesh.
    pcl::toPCLPointCloud2(trimmed, mesh.cloud);
    {
        std::vector<pcl::Vertices> remapped;
        remapped.reserve(mesh.polygons.size());
        for (const auto &poly : mesh.polygons) {
            bool complete = true;
            for (const auto vertexIndex : poly.vertices) {
                if (vertexIndex < 0 || static_cast<size_t>(vertexIndex) >= remap.size()
                    || remap[static_cast<size_t>(vertexIndex)] < 0) {
                    complete = false;
                    break;
                }
            }
            if (!complete) continue;
            pcl::Vertices face;
            face.vertices.reserve(poly.vertices.size());
            for (const auto vertexIndex : poly.vertices)
                face.vertices.push_back(remap[static_cast<size_t>(vertexIndex)]);
            remapped.push_back(face);
        }
        mesh.polygons = remapped;
    }

    qDebug() << "Poisson done. Vertices:" << mesh.cloud.width << "Faces:" << mesh.polygons.size();
    return mesh;
}

// ─── Texturing ───────────────────────────────────────────────────────────────

bool ReconstructionPostProcessor::projectWithP(const cv::Point3f& pw, const CameraParams& cam, cv::Point2f& uv)
{
    if (cam.P.empty() || cam.P.rows!=3 || cam.P.cols!=4) return false;
    cv::Mat X = (cv::Mat_<double>(4,1) << pw.x, pw.y, pw.z, 1.0);
    cv::Mat x = cam.P * X; // 3x1 double
    double w = x.at<double>(2);
    if (w <= 1e-6) return false;
    uv.x = float(x.at<double>(0) / w);
    uv.y = float(x.at<double>(1) / w);
    return true;
}

void ReconstructionPostProcessor::textureFromImages(
    std::vector<cv::Point3f>& pts,
    std::vector<cv::Vec3b>& cols,
    const std::vector<cv::Mat>& images,
    const std::vector<CameraParams>& cams)
{
    qDebug() << "Texturing point cloud from" << images.size() << "images (best-view)...";
    int noHit = 0;
    for (size_t i=0;i<pts.size();++i){
        float bestScore = -1e9f;
        cv::Vec3b bestCol(0,0,0);
        bool found = false;
        for (size_t j=0;j<images.size() && j<cams.size();++j){
            cv::Point2f uv;
            if (!projectWithP(pts[i], cams[j], uv)) continue;
            if (uv.x < 2 || uv.y < 2 || uv.x >= images[j].cols-2 || uv.y >= images[j].rows-2) continue;
            cv::Vec3b c = images[j].at<cv::Vec3b>((int)uv.y,(int)uv.x);
            if (c[0] < 5 && c[1] < 5 && c[2] < 5) continue; // bỏ nền đen templeRing
            float cx = images[j].cols*0.5f, cy = images[j].rows*0.5f;
            float score = -((uv.x-cx)*(uv.x-cx) + (uv.y-cy)*(uv.y-cy)); // gần tâm = tốt
            if (score > bestScore){
                bestScore = score;
                bestCol = c;
                found = true;
            }
        }
        if (found) cols[i] = bestCol;
        else noHit++;
    }
    qDebug() << "Texturing done: noHit =" << noHit << "/" << pts.size();
}

void ReconstructionPostProcessor::textureMeshFromImages(
    pcl::PolygonMesh& mesh,
    const std::vector<cv::Mat>& images,
    const std::vector<CameraParams>& cams,
    const QString &outputPath)
{
    pcl::PointCloud<pcl::PointXYZRGB> cloud;
    pcl::fromPCLPointCloud2(mesh.cloud, cloud);
    qDebug() << "Texturing mesh best-view Vertices:" << cloud.size();

    for (auto &pt : cloud.points){
        cv::Point3f pw(pt.x, pt.y, pt.z);
        float bestScore = -1e9f;
        cv::Vec3b bestCol(0,0,0);
        bool found = false;
        for (size_t j=0;j<images.size() && j<cams.size();++j){
            cv::Point2f uv;
            if (!projectWithP(pw, cams[j], uv)) continue;
            if (uv.x < 2 || uv.y < 2 || uv.x >= images[j].cols-2 || uv.y >= images[j].rows-2) continue;
            cv::Vec3b c = images[j].at<cv::Vec3b>((int)uv.y,(int)uv.x);
            if (c[0] < 5 && c[1] < 5 && c[2] < 5) continue;
            float cx = images[j].cols*0.5f, cy = images[j].rows*0.5f;
            float score = -((uv.x-cx)*(uv.x-cx) + (uv.y-cy)*(uv.y-cy));
            if (score > bestScore){
                bestScore = score;
                bestCol = c;
                found = true;
            }
        }
        if (found){ pt.b = bestCol[0]; pt.g = bestCol[1]; pt.r = bestCol[2]; }
    }

    pcl::toPCLPointCloud2(cloud, mesh.cloud);
    if (!outputPath.isEmpty()) {
        pcl::io::savePLYFileBinary(outputPath.toStdString(), mesh);
        qDebug() << "Texture mesh saved to" << outputPath << "Faces:" << mesh.polygons.size();
    }
}
