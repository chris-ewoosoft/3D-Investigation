#ifndef VIEWERSERVICE_H
#define VIEWERSERVICE_H

#include "IViewerService.h"
#include "Global.h"
#include <QTimer>

class ISceneService;
class SignalBus;

class APP_EXPORT ViewerService : public QObject, public IViewerService {
    Q_OBJECT
public:
    /// Collaborators are injected by the composition root (AppShell) instead
    /// of resolving them through IAppContext: the dependency set is explicit
    /// and a unit test only needs these two fakes.
    ViewerService(ISceneService* scene, SignalBus* bus, QObject* parent = nullptr);

    QString getCurrent2DImagePath() const override;
    void setCurrent2DImagePath(const QString &path) override;
    void setImageList(const QStringList &list, int index) override;
    void loadCurrentIndexImage() override;
    void onNextImage() override;
    void onPrevImage() override;
    void onAutoNext() override;
    void onAutoPrev() override;
    AIMode getCurrentAIMode() const override;
    void setAIMode(AIMode mode) override;

    bool isUpdateSceneEnabled() const override { return m_updateScene; }
    void setUpdateSceneEnabled(bool enabled) override { m_updateScene = enabled; }

private slots:
    void onAutoTimerTimeout();

private:
    ISceneService* m_scene = nullptr; ///< not owned (AppShell)
    SignalBus*     m_bus   = nullptr; ///< not owned (AppShell)
    QString current2DImagePath;
    QStringList imageFileList;
    int currentImageIndex;
    AIMode currentAIMode = AIMode::None;
    QTimer *autoTimer;
    bool isAutoNext;
    bool m_updateScene = true;
};

#endif // VIEWERSERVICE_H
