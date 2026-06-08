#ifndef AI_PROCESSOR_PLUGIN_H
#define AI_PROCESSOR_PLUGIN_H

#include <QObject>
#include "IPlugin.h"
#include <QAction>
#include <QProcess>
#include <QToolButton>
#include <QGroupBox>
#include <QFutureWatcher>

#include "IAppContext.h"
#include "IAIService.h"
#include <QTextBrowser>
#include <QProgressBar>
#include <QPushButton>

class AITrainDockWidget;
class AIProcessorRibbonUI;

class AIProcessorPlugin : public QObject, public IPlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID IPlugin_iid)
    Q_INTERFACES(IPlugin)

public:
    QString pluginName() const override { return "AI Processor Plugin"; }
    void initialize(IAppContext* context) override;
    void cleanup() override;
    int  loadOrder() const override { return 30; }

private slots:
    void onTrainModel();
    void onObjectDetection();
    void onSegmentation();
    void onHideAIResults();
    void onViewCharts();
    void updateActions();
    void onImageChanged(int index, int total);

private:
    IAppContext*   m_ctx         = nullptr;
    IAIService*    m_aiSvc       = nullptr;  ///< Looked up from ServiceRegistry
    CustomProgressDialog *m_progressDialog = nullptr;

    // Viewport and renderers;
    
    QAction *m_runDetAct = nullptr;
    QAction *m_hideDetAct = nullptr;
    QAction *m_runSegAct = nullptr;
    QAction *m_hideSegAct = nullptr;

    // Train AI UI
    AITrainDockWidget* m_trainDock = nullptr;

    // Tab buttons
    AIProcessorRibbonUI* m_ribbonUI = nullptr;

    // Async model loading
    QFutureWatcher<void>* m_modelLoadWatcher = nullptr;
    bool m_modelsLoading = false;
};

#endif // AI_PROCESSOR_PLUGIN_H
