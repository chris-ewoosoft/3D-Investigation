#include "AIYoloPostProcessor.h"

#include <algorithm>
#include <numeric>

#include <opencv2/imgproc.hpp>

namespace AIYoloPostProcessor {

cv::Size modelInputSize(const std::vector<int64_t> &shape)
{
    // NCHW: dimensions 2/3 are the spatial extent the network actually sees.
    if (shape.size() == static_cast<std::size_t>(kExpectedInputRank) && shape[2] > 0 && shape[3] > 0) {
        return cv::Size(static_cast<int>(shape[3]), static_cast<int>(shape[2]));
    }
    return cv::Size(kDefaultModelInputSize, kDefaultModelInputSize);
}

YoloDetections parsePredictions(const cv::Mat &predictions,
                                cv::Size imageSize,
                                cv::Size modelSize,
                                float scoreThreshold,
                                int maskCoefficientCount)
{
    YoloDetections detections;
    const int numClasses = predictions.cols - kExpectedBoxCoordinates - maskCoefficientCount;
    const float xFactor = imageSize.width / static_cast<float>(modelSize.width);
    const float yFactor = imageSize.height / static_cast<float>(modelSize.height);

    for (int row = 0; row < predictions.rows; ++row) {
        const float *data = predictions.ptr<float>(row);

        float maxScore = -1.0f;
        int classId = -1;
        for (int classIndex = 0; classIndex < numClasses; ++classIndex) {
            if (data[kExpectedBoxCoordinates + classIndex] > maxScore) {
                maxScore = data[kExpectedBoxCoordinates + classIndex];
                classId = classIndex;
            }
        }

        if (maxScore < scoreThreshold) continue;

        const float centerX = data[0];
        const float centerY = data[1];
        const float width = data[2];
        const float height = data[3];

        detections.classIds.push_back(classId);
        detections.confidences.push_back(maxScore);
        detections.boxes.push_back(cv::Rect(
            static_cast<int>((centerX - 0.5f * width) * xFactor),
            static_cast<int>((centerY - 0.5f * height) * yFactor),
            static_cast<int>(width * xFactor),
            static_cast<int>(height * yFactor)));

        if (maskCoefficientCount > 0) {
            detections.maskCoefficients.emplace_back(
                data + kExpectedBoxCoordinates + numClasses,
                data + kExpectedBoxCoordinates + numClasses + maskCoefficientCount);
        }
    }

    return detections;
}

std::vector<int> applyNms(const std::vector<cv::Rect> &boxes,
                          const std::vector<float> &confidences,
                          float nmsThreshold)
{
    std::vector<int> keptIndices;
    if (boxes.empty()) return keptIndices;

    std::vector<int> sortedIndices(boxes.size());
    std::iota(sortedIndices.begin(), sortedIndices.end(), 0);
    std::sort(sortedIndices.begin(), sortedIndices.end(),
              [&](int left, int right) { return confidences[left] > confidences[right]; });

    std::vector<bool> suppressed(boxes.size(), false);
    for (size_t sortedIndex = 0; sortedIndex < sortedIndices.size(); ++sortedIndex) {
        const int index = sortedIndices[sortedIndex];
        if (suppressed[index]) continue;

        keptIndices.push_back(index);

        for (size_t candidateIndex = sortedIndex + 1; candidateIndex < sortedIndices.size(); ++candidateIndex) {
            const int candidate = sortedIndices[candidateIndex];
            if (suppressed[candidate]) continue;

            const cv::Rect intersection = boxes[index] & boxes[candidate];
            const float intersectionArea = static_cast<float>(intersection.area());
            const float unionArea = static_cast<float>(boxes[index].area() + boxes[candidate].area()) - intersectionArea;
            const float iou = unionArea > 0.0f ? intersectionArea / unionArea : 0.0f;

            if (iou > nmsThreshold) {
                suppressed[candidate] = true;
            }
        }
    }

    return keptIndices;
}

cv::Rect sanitizeTrackingBox(const cv::Rect &box, cv::Size frameSize)
{
    if (frameSize.width <= 0 || frameSize.height <= 0) return {};

    const cv::Rect frame(0, 0, frameSize.width, frameSize.height);
    const cv::Rect clipped = box & frame;
    if (clipped.width < 4 || clipped.height < 4) return {};

    constexpr double kMaximumFrameFraction = 0.20;
    const double frameArea = static_cast<double>(frame.area());
    if (static_cast<double>(clipped.area()) > frameArea * kMaximumFrameFraction)
        return {};
    return clipped;
}

std::vector<cv::Rect> detectMotionBoxes(const cv::Mat &previousGray,
                                        const cv::Mat &currentGray)
{
    std::vector<cv::Rect> boxes;
    if (previousGray.empty() || currentGray.empty() ||
        previousGray.size() != currentGray.size() ||
        previousGray.type() != CV_8UC1 || currentGray.type() != CV_8UC1) {
        return boxes;
    }

    cv::Mat difference;
    cv::absdiff(previousGray, currentGray, difference);
    cv::threshold(difference, difference, 20, 255, cv::THRESH_BINARY);

    const cv::Mat kernel = cv::getStructuringElement(cv::MORPH_RECT, cv::Size(7, 7));
    cv::morphologyEx(difference, difference, cv::MORPH_CLOSE, kernel);
    cv::dilate(difference, difference, kernel, cv::Point(-1, -1), 2);

    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(difference, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);

    const double frameArea = static_cast<double>(currentGray.total());
    const double minimumArea = std::max(100.0, frameArea * 0.0005);
    for (const auto &contour : contours) {
        const cv::Rect candidate = cv::boundingRect(contour);
        if (static_cast<double>(candidate.area()) < minimumArea) continue;
        const cv::Rect sanitized = sanitizeTrackingBox(candidate, currentGray.size());
        if (!sanitized.empty()) boxes.push_back(sanitized);
    }
    return boxes;
}

cv::Scalar trackingColor(int trackId)
{
    // Offset every channel so track 0 is visible.  The previous multiplication
    // formula mapped id 0 to pure black, which looked like a broken ROI.
    const unsigned int id = static_cast<unsigned int>(std::max(trackId, 0));
    return cv::Scalar(64 + (id * 37U) % 160U,
                      64 + (id * 73U) % 160U,
                      64 + (id * 149U) % 160U);
}

}
