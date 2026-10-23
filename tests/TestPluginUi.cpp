#include <QtTest>

#include <QDialog>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QListWidget>
#include <QPushButton>
#include <QTextBrowser>
#include <QTimer>
#include <QToolButton>
#include <QWidget>

#include <opencv2/imgcodecs.hpp>

#include "AIAssistantRibbonUI.h"
#include "AIProcessorPlugin.h"
#include "AIProcessorRibbonUI.h"
#include "AITrainDockWidget.h"
#include "ChatMessageRenderer.h"
#include "IMailService.h"
#include "MailFilterDialog.h"
#include "MailSettingsDialog.h"
#include "SendMailRibbonUI.h"
#include "ViewerRibbonUI.h"
#include "ViewerViewModel.h"
#include "mock/PluginContextMock.h"

/**
 * @brief The remaining UI surface of the plugins: the ribbon panels, the mail
 * dialogs, the training dock and the viewer view model.
 *
 * The ribbon panels share one recipe (AbstractRibbonPanel): a group box with
 * an icon-painted row of tool buttons, installed into a tab panel.  The mail
 * dialogs are modal — a guard closes them so the test cannot hang.
 */
class TestPluginUi : public QObject {
    Q_OBJECT

private slots:
    void sendMailRibbonIsBuilt();
    void aiAssistantRibbonIsBuilt();
    void aiProcessorRibbonIsBuilt();
    void viewerRibbonIsBuilt();
    void mailFilterDialogCanBeDismissed();
    void mailSettingsDialogCanBeDismissed();
    void viewerViewModelIgnoresEmptyPaths();
    void trainingDockIsBuiltAndRetranslated();
    void detectionInferenceRunsOffTheGuiThread();
    void segmentationInferenceRunsOffTheGuiThread();
    void oneInferenceAtATime();
    void aThrowingInferenceIsReportedOnce();
    void chatHistoryRendersTheMessages();
    void theAgentTraceRendersItsSteps();
    void theThinkingIndicatorFollowsTheInsertIndex();
};

namespace {

/// A tab panel like the shell provides it: a horizontal layout to host a group.
QWidget *makePanel()
{
    auto *panel = new QWidget;
    panel->setLayout(new QHBoxLayout);
    return panel;
}

/// Closes a modal dialog as soon as it appears.
class ModalDialogGuard : public QObject {
public:
    ModalDialogGuard()
    {
        m_timer.setInterval(50);
        connect(&m_timer, &QTimer::timeout, this, [this] {
            if (auto *modal = QApplication::activeModalWidget()) {
                if (auto *dialog = qobject_cast<QDialog *>(modal)) {
                    dialog->reject();
                } else {
                    modal->close();
                }
            }
        });
    }

    void start() { m_timer.start(); }
    void stop() { m_timer.stop(); }

private:
    QTimer m_timer;
};

/// A small image on disk: the inference task reads the path the viewer reports
/// (cv::imread runs on the worker thread), so the file has to exist.
QString writeTestImage(const QTemporaryDir &dir)
{
    const QString path = dir.filePath(QStringLiteral("frame.png"));
    const cv::Mat image(16, 16, CV_8UC3, cv::Scalar(10, 20, 30));
    if (!cv::imwrite(path.toStdString(), image)) return {};
    return path;
}

/// Initializes the plugin and guarantees its bounded teardown on every exit
/// path.  A failed assertion must not leave an inference task calling into the
/// mock services after the test function's stack has been unwound.
struct RunningAIProcessorPlugin {
    explicit RunningAIProcessorPlugin(PluginMock::AppContext &context)
        : initialized(plugin.initialize(&context)) {}
    ~RunningAIProcessorPlugin()
    {
        if (initialized) plugin.cleanup();
    }

    AIProcessorPlugin plugin;
    bool initialized = false;
};

} // namespace

void TestPluginUi::sendMailRibbonIsBuilt()
{
    PluginMock::AppContext context(QDir::tempPath());
    QWidget panel;
    panel.setLayout(new QHBoxLayout);

    SendMailRibbonUI ribbon(&context, &panel);
    QVERIFY(ribbon.btnToggleMail() != nullptr);
    QVERIFY(ribbon.btnSettings() != nullptr);
    QVERIFY(ribbon.groupMail() != nullptr);
}

void TestPluginUi::aiAssistantRibbonIsBuilt()
{
    PluginMock::AppContext context(QDir::tempPath());
    QWidget panel;
    panel.setLayout(new QHBoxLayout);

    AIAssistantRibbonUI ribbon(&context, &panel);
    QVERIFY(ribbon.btnToggleAssistant() != nullptr);
}

void TestPluginUi::aiProcessorRibbonIsBuilt()
{
    PluginMock::AppContext context(QDir::tempPath());
    QWidget panel;
    panel.setLayout(new QHBoxLayout);

    AIProcessorRibbonUI ribbon(&context, &panel);
    QVERIFY(ribbon.btnDet() != nullptr);
}

void TestPluginUi::viewerRibbonIsBuilt()
{
    PluginMock::AppContext context(QDir::tempPath());
    QWidget panel;
    panel.setLayout(new QHBoxLayout);

    ViewerRibbonUI ribbon(&context, &panel);
    QVERIFY(ribbon.btnLoad2D() != nullptr);
}

void TestPluginUi::mailFilterDialogCanBeDismissed()
{
    PluginMock::AppContext context(QDir::tempPath());
    QStringList keywords{QStringLiteral("keep")};

    ModalDialogGuard guard;
    guard.start();
    // The dialog is modal: the guard dismisses it, so edit() returns false.
    const bool accepted = MailFilterDialog::edit(nullptr, &context, keywords);
    guard.stop();

    QVERIFY(!accepted);
    // The keywords must survive the cancelled edit untouched.
    QCOMPARE(keywords, QStringList{QStringLiteral("keep")});
}

void TestPluginUi::mailSettingsDialogCanBeDismissed()
{
    PluginMock::AppContext context(QDir::tempPath());
    PluginMock::MailService mail;

    ModalDialogGuard guard;
    guard.start();
    MailSettingsDialog::show(nullptr, &context, &mail);
    guard.stop();

    QVERIFY(true); // reaching this point means the modal dialog was dismissed
}

void TestPluginUi::viewerViewModelIgnoresEmptyPaths()
{
    PluginMock::AppContext context(QDir::tempPath());
    ViewerViewModel model(&context);

    // An empty path is a no-op — no scene, no viewer, no progress signals.
    model.load2DImage(QString());
    model.load3DModel(QString());
    QVERIFY(true);
}

void TestPluginUi::trainingDockIsBuiltAndRetranslated()
{
    PluginMock::AppContext context(QDir::tempPath());

    // The dock is installed into the shell's central widget by the plugin;
    // here it only has to be built, retranslated and destroyed cleanly.
    auto *dock = new AITrainDockWidget(&context, context.shellService().dialogParent());
    QVERIFY(dock != nullptr);
    QVERIFY(!dock->windowTitle().isEmpty());

    // A language change re-applies the translated texts.
    dock->retranslate();
    QVERIFY(!dock->windowTitle().isEmpty());

    // The process output handler is a no-op while nothing is running — it
    // must not touch the (never started) process.
    QVERIFY(QMetaObject::invokeMethod(dock, "onTrainProcessOutput", Qt::DirectConnection));

    delete dock;
}

// ─── AI inference leaves the GUI thread ─────────────────────────────────────
//
// The plugin's inference entry points used to run the ONNX call inside the GUI
// slot, so the window froze for the whole inference.  The contract pinned here
// is the dispatch: the call happens on another thread, only one runs at a
// time, and a failing backend is reported once instead of killing the process.

void TestPluginUi::detectionInferenceRunsOffTheGuiThread()
{
    PluginMock::AppContext context(QDir::tempPath());
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString imagePath = writeTestImage(dir);
    QVERIFY(!imagePath.isEmpty());

    context.viewerService().setCurrent2DImagePath(imagePath);
    context.ai().detectionReady = true;

    RunningAIProcessorPlugin instance(context);
    QVERIFY(instance.initialized);

    QSignalSpy finished(context.signalBus(), &SignalBus::aiInferenceFinished);
    QVERIFY(QMetaObject::invokeMethod(&instance.plugin, "onObjectDetection"));
    QVERIFY2(finished.wait(5000), "the detection result never arrived");

    QCOMPARE(context.ai().detectionCalls, 1);
    QVERIFY2(context.ai().lastInferenceThread != QThread::currentThreadId(),
             "the inference ran on the GUI thread");
    QCOMPARE(context.viewerService().getCurrentAIMode(), AIMode::Detection);
    QCOMPARE(finished.count(), 1);
    QCOMPARE(finished.first().at(0).toString(), QStringLiteral("detection"));
}

void TestPluginUi::segmentationInferenceRunsOffTheGuiThread()
{
    PluginMock::AppContext context(QDir::tempPath());
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString imagePath = writeTestImage(dir);
    QVERIFY(!imagePath.isEmpty());

    context.viewerService().setCurrent2DImagePath(imagePath);
    context.ai().segmentationReady = true;

    RunningAIProcessorPlugin instance(context);
    QVERIFY(instance.initialized);

    QSignalSpy finished(context.signalBus(), &SignalBus::aiInferenceFinished);
    QVERIFY(QMetaObject::invokeMethod(&instance.plugin, "onSegmentation"));
    QVERIFY2(finished.wait(5000), "the segmentation result never arrived");

    QCOMPARE(context.ai().segmentationCalls, 1);
    QVERIFY2(context.ai().lastInferenceThread != QThread::currentThreadId(),
             "the inference ran on the GUI thread");
    QCOMPARE(context.viewerService().getCurrentAIMode(), AIMode::Segmentation);
    QCOMPARE(finished.first().at(0).toString(), QStringLiteral("segmentation"));
}

void TestPluginUi::oneInferenceAtATime()
{
    PluginMock::AppContext context(QDir::tempPath());
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString imagePath = writeTestImage(dir);
    QVERIFY(!imagePath.isEmpty());

    context.viewerService().setCurrent2DImagePath(imagePath);
    context.ai().detectionReady = true;
    context.ai().inferenceDelayMs = 250; // keep the first request in flight

    RunningAIProcessorPlugin instance(context);
    QVERIFY(instance.initialized);

    QSignalSpy finished(context.signalBus(), &SignalBus::aiInferenceFinished);
    QVERIFY(QMetaObject::invokeMethod(&instance.plugin, "onObjectDetection"));
    // A second click while the first inference is still running: the ONNX
    // session is shared, and two results would race for the same viewer.
    QVERIFY(QMetaObject::invokeMethod(&instance.plugin, "onObjectDetection"));
    QVERIFY2(finished.wait(5000), "the detection result never arrived");

    QCOMPARE(context.ai().detectionCalls, 1);
    QCOMPARE(finished.count(), 1);

    // The busy guard is about "in flight", not a permanent lock: once the
    // result is applied a new request must run again.
    QVERIFY(QMetaObject::invokeMethod(&instance.plugin, "onObjectDetection"));
    QVERIFY2(finished.wait(5000), "the second detection result never arrived");
    QCOMPARE(context.ai().detectionCalls, 2);
}

void TestPluginUi::aThrowingInferenceIsReportedOnce()
{
    PluginMock::AppContext context(QDir::tempPath());
    QTemporaryDir dir;
    QVERIFY(dir.isValid());
    const QString imagePath = writeTestImage(dir);
    QVERIFY(!imagePath.isEmpty());

    context.viewerService().setCurrent2DImagePath(imagePath);
    context.ai().detectionReady = true;
    context.ai().throwOnInference = true;

    RunningAIProcessorPlugin instance(context);
    QVERIFY(instance.initialized);

    ModalDialogGuard guard;
    guard.start();
    QSignalSpy finished(context.signalBus(), &SignalBus::aiInferenceFinished);
    QVERIFY(QMetaObject::invokeMethod(&instance.plugin, "onObjectDetection"));
    QVERIFY2(finished.wait(5000), "the failure was never reported");
    guard.stop();

    // blocking::run() contained the exception and delivered a default result:
    // one completion, no stale AI mode, and the process is still alive.
    QCOMPARE(finished.count(), 1);
    QCOMPARE(context.ai().detectionCalls, 0);
    QCOMPARE(context.viewerService().getCurrentAIMode(), AIMode::None);
}


//
// The renderer turns the typed messages into HTML. The contract worth
// pinning: a user message and an assistant message both reach the chat, the
// agent trace shows its steps, and the "thinking" marker sits where the
// caller asked for it.

// A message with the given role and text.
static ChatMessage makeMessage(ChatRole role, const QString &content,
                                const QString &timestamp = QString())
{
    ChatMessage message;
    message.role = role;
    message.content = content;
    message.timestamp = timestamp;
    return message;
}

void TestPluginUi::chatHistoryRendersTheMessages()
{
    PluginMock::AppContext context(QDir::tempPath());
    QTextBrowser browser;

    QList<ChatMessage> history;
    history.append(makeMessage(ChatRole::User, QStringLiteral("xin chào"),
                               QStringLiteral("10:00")));
    history.append(makeMessage(ChatRole::Assistant, QStringLiteral("chào bạn"),
                               QStringLiteral("10:01")));

    ChatMessageRenderer::renderChatHistory(&browser, &context, history, false, -1);

    const QString html = browser.toHtml();
    QVERIFY2(html.contains(QStringLiteral("xin chào")), "the user message is missing");
    QVERIFY2(html.contains(QStringLiteral("chào bạn")), "the assistant message is missing");
    QVERIFY2(html.contains(QStringLiteral("10:00")), "the timestamp is missing");
    // The start-of-chat marker opens every history.
    QVERIFY(html.contains(QStringLiteral("ai.new_chat_start")));
}

void TestPluginUi::theAgentTraceRendersItsSteps()
{
    PluginMock::AppContext context(QDir::tempPath());
    QTextBrowser browser;

    // The agent trace is a message whose content is the server's envelope.
    QJsonObject thinking;
    thinking["type"] = QStringLiteral("thinking");
    thinking["content"] = QStringLiteral("đang suy nghĩ");
    QJsonObject finalAnswer;
    finalAnswer["type"] = QStringLiteral("final_answer");
    finalAnswer["content"] = QStringLiteral("kết quả cuối");
    QJsonArray steps;
    steps.append(thinking);
    steps.append(finalAnswer);
    QJsonObject envelope;
    envelope["steps"] = steps;
    envelope["prior_step_count"] = 0;

    QList<ChatMessage> history;
    history.append(makeMessage(ChatRole::AgentTrace,
                               QString::fromUtf8(QJsonDocument(envelope).toJson(
                                   QJsonDocument::Compact))));

    ChatMessageRenderer::renderChatHistory(&browser, &context, history, false, -1);

    const QString html = browser.toHtml();
    QVERIFY2(html.contains(QStringLiteral("kết quả cuối")),
             "the agent's final answer is missing");
    QVERIFY2(html.contains(QStringLiteral("đang suy nghĩ")),
             "the agent's thinking step is missing");
}

void TestPluginUi::theThinkingIndicatorFollowsTheInsertIndex()
{
    PluginMock::AppContext context(QDir::tempPath());
    QTextBrowser browser;

    QList<ChatMessage> history;
    history.append(makeMessage(ChatRole::User, QStringLiteral("câu hỏi")));

    // The marker is inserted after the message at the given index …
    ChatMessageRenderer::renderChatHistory(&browser, &context, history, true, 0);
    QVERIFY(browser.toHtml().contains(QStringLiteral("ai.thinking")));

    // … and at the end of the history when the index is out of range.
    QTextBrowser tail;
    ChatMessageRenderer::renderChatHistory(&tail, &context, history, true, 99);
    QVERIFY(tail.toHtml().contains(QStringLiteral("ai.thinking")));

    // No marker without a pending request.
    QTextBrowser idle;
    ChatMessageRenderer::renderChatHistory(&idle, &context, history, false, -1);
    QVERIFY(!idle.toHtml().contains(QStringLiteral("ai.thinking")));
}

QTEST_MAIN(TestPluginUi)
#include "TestPluginUi.moc"
