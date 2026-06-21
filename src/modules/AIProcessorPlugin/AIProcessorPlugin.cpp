#include "AIProcessorPlugin.h"
#include "IAppContext.h"
#include "ISettingsService.h"
#include "IViewerService.h"
#include "ISceneService.h"
#include "IAIService.h"
#include "SignalBus.h"
#include "Image2DLoader.h"
#include "AITrainDockWidget.h"
#include "AIProcessorRibbonUI.h"
#include "LanguageManager.h"
#include "IconFactory.h"
#include "ModernMessageBox.h"
#include <QDesktopServices>
#include <QDir>
#include <QFileInfo>
#include <QMenu>
#include <QProcess>
#include <QUrl>
#include <QDateTime>
#include <QFile>
#include <QDockWidget>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QLabel>
#include <QProgressBar>
#include <QPushButton>
#include <QTextBrowser>
#include <QApplication>
#include <QVTKOpenGLNativeWidget.h>
#include <QTimer>
#include <QtConcurrent>
#include <QGroupBox>
#include <QStyle>
#include "AppConfig.h"
#include <vtkRenderWindow.h>
#include <opencv2/opencv.hpp>
#include "../../utils/CustomProgressDialog.h"

void AIProcessorPlugin::initialize(IAppContext *context) {
  m_ctx = context;

  // Look up IAIService từ ServiceRegistry — không dùng concrete AIProcessor
  m_aiSvc = m_ctx->services()->get<IAIService>();
  if (!m_aiSvc) {
      qWarning() << "[AIProcessorPlugin] IAIService not found in ServiceRegistry!";
  }

  m_progressDialog = new CustomProgressDialog(m_ctx->mainWindow());

  m_trainDock = new AITrainDockWidget(m_ctx, m_ctx->mainWindow());

  // Load models in background thread to avoid blocking the UI.
  // TRT engine compilation on first run can take 5-20 minutes.
  if (m_aiSvc) {
      QString modelsPath = AppConfig::instance().modelsDir();
      QString detPath = modelsPath + "/yolo11n.onnx";
      QString segPath = modelsPath + "/yolo11n-seg.onnx";
      bool detExists = QFile::exists(detPath);
      bool segExists = QFile::exists(segPath);

      if (detExists || segExists) {
          m_modelsLoading = true;
          m_cancelModelLoad.store(false);

          // Set actions to "Loading" state immediately
          if (m_runDetAct) m_runDetAct->setEnabled(false);
          if (m_runSegAct) m_runSegAct->setEnabled(false);

          // Setup watcher — finished() fires on main thread
          m_modelLoadWatcher = new QFutureWatcher<void>(this);
          connect(m_modelLoadWatcher, &QFutureWatcher<void>::finished, this, [this]() {
              m_modelsLoading = false;
              if (m_progressDialog) m_progressDialog->hide();
              // Disconnect stop handler for model loading so it doesn't
              // interfere with other uses of the same dialog (e.g. TensorBoard).
              if (m_modelLoadStopConn) {
                  disconnect(m_modelLoadStopConn);
                  m_modelLoadStopConn = {};
              }
              if (m_cancelModelLoad.load()) {
                  qDebug() << "[AIProcessorPlugin] Model loading was cancelled by user.";
              } else {
                  qDebug() << "[AIProcessorPlugin] Background model loading completed.";
                  if (m_aiSvc && m_aiSvc->isDetectionReady())
                      emit m_ctx->signalBus()->aiModelLoaded("detection");
                  if (m_aiSvc && m_aiSvc->isSegmentationReady())
                      emit m_ctx->signalBus()->aiModelLoaded("segmentation");
              }
              updateActions(); // Re-enable buttons
          });

          if (m_progressDialog) {
              // Connect Stop button to cancel model loading
              m_modelLoadStopConn = connect(m_progressDialog, &CustomProgressDialog::stopRequested, this, [this]() {
                  m_cancelModelLoad.store(true);
                  m_modelsLoading = false;
                  if (m_progressDialog) m_progressDialog->hide();
                  qDebug() << "[AIProcessorPlugin] User requested stop — model loading cancelled.";
                  updateActions();
              });

              // Show which model loads first
              QString firstLabel;
              if (detExists && segExists)
                  firstLabel = m_ctx->translate("aiproc.loading_det").isEmpty()
                               ? tr("Loading model Detection...")
                               : m_ctx->translate("aiproc.loading_det");
              else if (detExists)
                  firstLabel = m_ctx->translate("aiproc.loading_det").isEmpty()
                               ? tr("Loading model Detection...")
                               : m_ctx->translate("aiproc.loading_det");
              else
                  firstLabel = m_ctx->translate("aiproc.loading_seg").isEmpty()
                               ? tr("Loading model Segmentation...")
                               : m_ctx->translate("aiproc.loading_seg");
              m_progressDialog->setLabelText(firstLabel);
              m_progressDialog->setRange(0, 0);   // indeterminate / marquee
              m_progressDialog->show();
              m_progressDialog->centerOnWidget(m_ctx->mainWindow());
          }

          // Run on thread pool — does NOT block main thread
          IAIService* svc   = m_aiSvc;
          auto *dlg         = m_progressDialog;
          auto *ctx         = m_ctx;
          bool _detExists   = detExists;
          bool _segExists   = segExists;
          auto *cancelFlag  = &m_cancelModelLoad;

          QFuture<void> future = QtConcurrent::run([svc, dlg, ctx, detPath, segPath,
                                                    _detExists, _segExists, cancelFlag]() {
              if (_detExists && !cancelFlag->load()) {
                  // Update label to Detection on main thread
                  QString detMsg = ctx->translate("aiproc.loading_det");
                  if (detMsg.isEmpty()) detMsg = "Loading model Detection...";
                  QMetaObject::invokeMethod(dlg, [dlg, detMsg]() {
                      if (dlg) dlg->setLabelText(detMsg);
                  }, Qt::QueuedConnection);
                  svc->loadDetectionModel(detPath);
              }
              if (_segExists && !cancelFlag->load()) {
                  // Update label to Segmentation on main thread
                  QString segMsg = ctx->translate("aiproc.loading_seg");
                  if (segMsg.isEmpty()) segMsg = "Loading model Segmentation...";
                  QMetaObject::invokeMethod(dlg, [dlg, segMsg]() {
                      if (dlg) dlg->setLabelText(segMsg);
                  }, Qt::QueuedConnection);
                  svc->loadSegmentationModel(segPath);
              }
          });
          m_modelLoadWatcher->setFuture(future);
      }
  }

  QMenu *aiMenu = m_ctx->getMenu("ai_menu");
  aiMenu->clear();

  // Detection sub-menu with icons
  QMenu *detMenu = aiMenu->addMenu(IconFactory::createModern("🔍", QColor("#f59e0b"), QColor("#d97706")),
                                   "Object Detection");
  m_runDetAct = detMenu->addAction(
      IconFactory::createModern("🔍", QColor("#f59e0b"), QColor("#d97706")),
      m_ctx->translate("ai.run_detection"), this, &AIProcessorPlugin::onObjectDetection);
  m_hideDetAct = detMenu->addAction(
      IconFactory::createModern("👁️", QColor("#6b7280"), QColor("#4b5563")),
      m_ctx->translate("ai.hide_results"), this, &AIProcessorPlugin::onHideAIResults);

  // Segmentation sub-menu with icons
  QMenu *segMenu = aiMenu->addMenu(IconFactory::createModern("🎯", QColor("#10b981"), QColor("#059669")),
                                   "Segmentation");
  m_runSegAct = segMenu->addAction(
      IconFactory::createModern("🎯", QColor("#10b981"), QColor("#059669")),
      m_ctx->translate("ai.run_segmentation"), this, &AIProcessorPlugin::onSegmentation);
  m_hideSegAct = segMenu->addAction(
      IconFactory::createModern("👁️", QColor("#6b7280"), QColor("#4b5563")),
      m_ctx->translate("ai.hide_results"), this, &AIProcessorPlugin::onHideAIResults);

  aiMenu->addSeparator();
  aiMenu->addAction(
      IconFactory::createModern("⚙️", QColor("#8b5cf6"), QColor("#7c3aed")),
      m_ctx->translate("ai.training"), this, &AIProcessorPlugin::onTrainModel);
  aiMenu->addAction(
      IconFactory::createModern("📊", QColor("#06b6d4"), QColor("#0891b2")),
      m_ctx->translate("ai.charts"), this, &AIProcessorPlugin::onViewCharts);

  if (QWidget* panel = m_ctx->getTabPanel("tab.ai")) {
      m_ribbonUI = new AIProcessorRibbonUI(m_ctx, panel, this);
      
      connect(m_ribbonUI->btnDet(), &QToolButton::clicked, this, &AIProcessorPlugin::onObjectDetection);
      connect(m_ribbonUI->btnSeg(), &QToolButton::clicked, this, &AIProcessorPlugin::onSegmentation);
      connect(m_ribbonUI->btnHide(), &QToolButton::clicked, this, &AIProcessorPlugin::onHideAIResults);
      connect(m_ribbonUI->btnTrain(), &QToolButton::clicked, this, &AIProcessorPlugin::onTrainModel);
      connect(m_ribbonUI->btnChart(), &QToolButton::clicked, this, &AIProcessorPlugin::onViewCharts);
  }

  connect(m_ctx->signalBus(), &SignalBus::stateChanged, this,
          &AIProcessorPlugin::updateActions);
  connect(m_ctx->signalBus(), &SignalBus::languageChanged, this,
          &AIProcessorPlugin::updateActions);
  connect(m_ctx->signalBus(), &SignalBus::imageIndexChanged, this,
          &AIProcessorPlugin::onImageChanged);
  
  m_trainDock = new AITrainDockWidget(m_ctx);
  m_ctx->mainWindow()->addDockWidget(Qt::RightDockWidgetArea, m_trainDock);
  
  updateActions();
}

void AIProcessorPlugin::cleanup() {}

void AIProcessorPlugin::onTrainModel() {
    if (m_trainDock) {
        m_trainDock->startTraining();
    }
}

void AIProcessorPlugin::onObjectDetection() {
  QString currentImg = m_ctx->viewer()->getCurrent2DImagePath();
  if (currentImg.isEmpty()) {
      ModernMessageBox::warning(m_ctx->mainWindow(), m_ctx->translate("common.warning"), m_ctx->translate("aiproc.load_img_warn"));
      return;
  }
  
  // Clear any active 3D models before showing 2D results
  m_ctx->scene()->clear3DModel();
  m_ctx->scene()->clearPointCloud();
  m_ctx->scene()->resetToSingleRenderer();

  if (!m_aiSvc || !m_aiSvc->isDetectionReady()) {
      ModernMessageBox::warning(m_ctx->mainWindow(), m_ctx->translate("common.error"), m_ctx->translate("aiproc.model_not_loaded_warn"));
      return;
  }
  emit m_ctx->signalBus()->aiInferenceStarted("detection");
  cv::Mat res = m_aiSvc->runDetection(cv::imread(currentImg.toStdString()));
  emit m_ctx->signalBus()->aiInferenceFinished("detection");
  QString tp = QApplication::applicationDirPath() + "/temp_ai_result.png";
  cv::imwrite(tp.toStdString(), res);

  m_ctx->viewer()->setAIMode(AIMode::Detection);
  m_ctx->scene()->setTextureActor(Image2DLoader::load(tp));

  // Log prediction image
  QString currentDate = QDateTime::currentDateTime().toString("yyyy-MM-dd");
  QString predictDir = AppConfig::instance().predictDir("detection") + "/" + currentDate;
  QDir().mkpath(predictDir);
  QString logPath = predictDir + "/" + QFileInfo(currentImg).baseName() + "_" + QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss") + ".png";
  cv::imwrite(logPath.toStdString(), res);

  m_ctx->scene()->vtkWidget()->renderWindow()->Render();
  m_ctx->updateMenuStates();
}

void AIProcessorPlugin::onSegmentation() {
  QString currentImg = m_ctx->viewer()->getCurrent2DImagePath();
  if (currentImg.isEmpty()) {
      ModernMessageBox::warning(m_ctx->mainWindow(), m_ctx->translate("common.warning"), m_ctx->translate("aiproc.load_img_warn"));
      return;
  }

  // Clear any active 3D models before showing 2D results
  m_ctx->scene()->clear3DModel();
  m_ctx->scene()->clearPointCloud();
  m_ctx->scene()->resetToSingleRenderer();

  if (!m_aiSvc || !m_aiSvc->isSegmentationReady()) {
      ModernMessageBox::warning(m_ctx->mainWindow(), m_ctx->translate("common.error"), m_ctx->translate("aiproc.model_not_loaded_warn"));
      return;
  }
  emit m_ctx->signalBus()->aiInferenceStarted("segmentation");
  cv::Mat res = m_aiSvc->runSegmentation(cv::imread(currentImg.toStdString()));
  emit m_ctx->signalBus()->aiInferenceFinished("segmentation");
  QString tp = QApplication::applicationDirPath() + "/temp_ai_result.png";
  cv::imwrite(tp.toStdString(), res);

  m_ctx->viewer()->setAIMode(AIMode::Segmentation);
  m_ctx->scene()->setTextureActor(Image2DLoader::load(tp));

  // Log prediction image
  QString currentDate = QDateTime::currentDateTime().toString("yyyy-MM-dd");
  QString predictDir = AppConfig::instance().predictDir("segmentation") + "/" + currentDate;
  QDir().mkpath(predictDir);
  QString logPath = predictDir + "/" + QFileInfo(currentImg).baseName() + "_" + QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss") + ".png";
  cv::imwrite(logPath.toStdString(), res);

  m_ctx->scene()->vtkWidget()->renderWindow()->Render();
  m_ctx->updateMenuStates();
}

void AIProcessorPlugin::onHideAIResults() {
    const QString originalPath = m_ctx->viewer()->getCurrent2DImagePath();
    m_ctx->viewer()->setAIMode(AIMode::None);
    if (!originalPath.isEmpty() && QFile::exists(originalPath)) {
        m_ctx->scene()->setTextureActor(Image2DLoader::load(originalPath));
        m_ctx->scene()->vtkWidget()->renderWindow()->Render();
    } else {
        m_ctx->viewer()->loadCurrentIndexImage();
    }
    m_ctx->updateMenuStates();
}

void AIProcessorPlugin::onViewCharts() {
  QProcess::execute("taskkill", QStringList() << "/F" << "/IM" << "tensorboard.exe");
  QString aiTrainingDir = AppConfig::instance().aiTrainingDir();
  QString runsDir = QDir::cleanPath(aiTrainingDir + "/runs");
  runsDir = QDir::toNativeSeparators(runsDir);

  QProcess::startDetached("python",
                          QStringList() << "-m" << "tensorboard.main"
                                        << "--logdir" << runsDir << "--port" << "6006",
                          QDir::cleanPath(aiTrainingDir));

  const int totalSeconds = 12;

  if (!m_progressDialog) return;

  m_progressDialog->setLabelText(
      m_ctx->translate("aiproc.tb_seconds").arg(totalSeconds));
  m_progressDialog->setValue(0);
  m_progressDialog->show();
  m_progressDialog->centerOnWidget(); // Centers on MainWindow by default
  QApplication::processEvents();

  // Shared countdown state via QTimer on heap to avoid dangling refs
  QTimer *timer = new QTimer(m_progressDialog);
  int *remaining = new int(totalSeconds);

  connect(m_progressDialog, &CustomProgressDialog::stopRequested, timer,
          [timer, remaining, this]() {
            timer->stop();
            timer->deleteLater();
            delete remaining;
            if (m_progressDialog) m_progressDialog->hide();
            ModernMessageBox::information(
                m_ctx->mainWindow(), m_ctx->translate("aiproc.tb_cancelled"),
                m_ctx->translate("aiproc.tb_cancelled_desc"));
          });

  timer->setInterval(1000);
  connect(timer, &QTimer::timeout, this,
      [this, timer, remaining, totalSeconds]() {
          (*remaining)--;
          if (m_progressDialog) {
              int progressValue = ((totalSeconds - *remaining) * 100) / totalSeconds;
              m_progressDialog->setValue(progressValue);
          }
          if (*remaining <= 0) {
              timer->stop();
              timer->deleteLater();
              delete remaining;
              m_progressDialog->hide();
              m_progressDialog->reset();
              QDesktopServices::openUrl(QUrl("http://localhost:6006/"));
          } else {
              if (m_progressDialog) {
                  m_progressDialog->setLabelText(
                      m_ctx->translate("aiproc.tb_seconds").arg(*remaining));
              }
          }
      });

  timer->start();
}

void AIProcessorPlugin::onImageChanged(int index, int total) {
    AIMode mode = m_ctx->viewer()->getCurrentAIMode();
    if (mode == AIMode::Detection) {
        onObjectDetection();
    } else if (mode == AIMode::Segmentation) {
        onSegmentation();
    }
}

void AIProcessorPlugin::updateActions() {
    QMenu *aiMenu = m_ctx->getMenu("ai_menu");
    if (aiMenu) {
        aiMenu->setTitle(m_ctx->translate("ai.menu"));
    }
    if (!m_ctx->mainWindow() || !m_runDetAct) return;
  AIMode mode = m_ctx->viewer()->getCurrentAIMode();
  bool has2D = !m_ctx->viewer()->getCurrent2DImagePath().isEmpty();

  bool detReady = m_aiSvc && m_aiSvc->isDetectionReady();
  bool segReady = m_aiSvc && m_aiSvc->isSegmentationReady();

  if (m_modelsLoading) {
      m_runDetAct->setEnabled(false);
      m_runSegAct->setEnabled(false);
      if (m_ribbonUI) {
          m_ribbonUI->btnDet()->setText(m_ctx->translate("ai.run_detection"));
          m_ribbonUI->btnDet()->setEnabled(false);
          m_ribbonUI->btnSeg()->setText(m_ctx->translate("ai.run_segmentation"));
          m_ribbonUI->btnSeg()->setEnabled(false);
      }
  } else {
      m_runDetAct->setEnabled(mode != AIMode::Detection && has2D && detReady);
      m_runSegAct->setEnabled(mode != AIMode::Segmentation && has2D && segReady);
      if (m_ribbonUI) {
          m_ribbonUI->btnDet()->setText(m_ctx->translate("ai.run_detection"));
          m_ribbonUI->btnDet()->setEnabled(mode != AIMode::Detection && has2D && detReady);
          m_ribbonUI->btnSeg()->setText(m_ctx->translate("ai.run_segmentation"));
          m_ribbonUI->btnSeg()->setEnabled(mode != AIMode::Segmentation && has2D && segReady);
      }
  }

  m_hideDetAct->setEnabled(mode == AIMode::Detection);
  m_hideSegAct->setEnabled(mode == AIMode::Segmentation);

  if (m_ribbonUI) {
      m_ribbonUI->btnHide()->setText(m_ctx->translate("ai.hide_results"));
      m_ribbonUI->btnHide()->setEnabled(mode != AIMode::None);
      m_ribbonUI->btnTrain()->setText(m_ctx->translate("ai.training"));
      m_ribbonUI->btnChart()->setText(m_ctx->translate("ai.charts"));
      
      // Update GroupBox titles
      if (QLabel *lbl = m_ribbonUI->groupAI()->findChild<QLabel*>("groupTitleLabel")) {
          lbl->setText(m_ctx->translate("ai.menu"));
      }
      if (QLabel *lbl = m_ribbonUI->groupTrain()->findChild<QLabel*>("groupTitleLabel")) {
          lbl->setText(m_ctx->translate("ai.training"));
      }
  }
}
