#ifndef VIEWERSERVICE_H
#define VIEWERSERVICE_H

#include "IAppContext.h"
#include "IViewerService.h"
#include "SignalBus.h"
#include <QTimer>

class ViewerService : public QObject, public IViewerService {
    Q_OBJECT
public:
    ViewerService(IAppContext* ctx, QObject* parent = nullptr);

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

private slots:
    void onAutoTimerTimeout();

private:
    IAppContext* m_ctx;
    QString current2DImagePath;
    QStringList imageFileList;
    int currentImageIndex;
    AIMode currentAIMode;
    QTimer *autoTimer;
    bool isAutoNext;
};

#endif // VIEWERSERVICE_H
