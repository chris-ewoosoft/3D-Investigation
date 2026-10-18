#ifndef AI_CONSTANTS_H
#define AI_CONSTANTS_H
#include <QString>
#include <QStringList>
namespace AppConstants { namespace AIAssistant {
    constexpr int DEFAULT_DOCK_WIDTH        = 520;
    constexpr int SESSION_PANEL_MIN_WIDTH   = 120;
    constexpr int SESSION_PANEL_MAX_WIDTH   = 280;
    constexpr int MAX_ATTACHMENT_SIZE_MB     = 10;
    constexpr int VISION_MODEL_INDEX         = 3;
    inline QStringList modelNames() {
        return {
            QStringLiteral("Qwen3-8B (Q4_K_M) — Text / Agent / Coder"),
            QStringLiteral("Qwen2.5-7B (Q4_K_M) — Text"),
            QStringLiteral("Qwen2.5-coder-7B (Q4_K_M) — Coder"),
            QStringLiteral("Qwen2.5-VL-7B (Q4_K_M) — Vision 👁️")
        };
    }
} namespace AIServer {
    constexpr int    SERVER_PORT                = 8080;
    constexpr double DEFAULT_TEMPERATURE        = 0.7;
    constexpr int    DEFAULT_MAX_TOKENS         = 512;
    constexpr int    STOP_SERVER_TIMEOUT_MS     = 3000;
    constexpr int    TEXT_INFERENCE_TIMEOUT_MS  = 1800000;
    constexpr int    VISION_INFERENCE_TIMEOUT_MS = 1800000;
    constexpr int    INFERENCE_TIMEOUT_MS       = TEXT_INFERENCE_TIMEOUT_MS;
    inline QString baseUrl() {
        return QStringLiteral("http://127.0.0.1:%1").arg(SERVER_PORT);
    }
    inline QString adminEndpoint(const QString &path) {
        return QStringLiteral("http://127.0.0.1:%1/admin/%2").arg(SERVER_PORT).arg(path);
    }
    inline QString chatbotScript() {
        return QStringLiteral("src/ai_assistant/bootstrap/server_manager.py");
    }
} namespace AIProcessor {
    inline QString detectionModelFile()     { return QStringLiteral("yolo11n.onnx"); }
    inline QString segmentationModelFile()  { return QStringLiteral("yolo11n-seg.onnx"); }
    inline QString trackingModelFile()      { return QStringLiteral("yolo11x-tracking.onnx"); }
    inline QString trainScript()            { return QStringLiteral("TrainModel.py"); }
    constexpr int    TENSORBOARD_PORT           = 6006;
    constexpr int    TENSORBOARD_WAIT_SECONDS   = 30;
    inline QString tensorboardUrl() {
        return QStringLiteral("http://localhost:%1/").arg(TENSORBOARD_PORT);
    }
} namespace Chat {
    constexpr int SESSION_TITLE_MAX_LENGTH = 30;
} namespace Format {
    inline QString dateForFolder()      { return QStringLiteral("yyyy-MM-dd"); }
    inline QString dateTimeForFile()    { return QStringLiteral("yyyyMMdd_HHmmss"); }
    inline QString sessionDateTime()    { return QStringLiteral("dd/MM HH:mm"); }
    inline QString chatTimestamp()      { return QStringLiteral("HH:mm"); }
} }
#endif
