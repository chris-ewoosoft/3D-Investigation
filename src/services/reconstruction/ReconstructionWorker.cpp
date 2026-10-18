#include "ReconstructionWorker.h"
#include "ReconstructionService.h"

void ReconstructionWorker::run()
{
    // PHẢI gọi runPipeline() (lõi đồng bộ dành cho worker), KHÔNG gọi
    // reconstruct(): entry đó là bộ đếm async — nó thấy m_running==true
    // (do lần gọi gốc đã set) và return false ngay, không chạy pipeline nào.
    const bool ok = m_service->runPipeline();
    emit finished(ok);
}
