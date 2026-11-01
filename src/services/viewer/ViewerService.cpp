#include "ViewerService.h"
#include "ISceneService.h"
#include "SignalBus.h"
#include "Image2DLoader.h"
#include <QFileInfo>
#include <QVTKOpenGLNativeWidget.h>
#include <vtkRenderWindow.h>

ViewerService::ViewerService(ISceneService* scene, SignalBus* bus, QObject* parent) 
    : QObject(parent), m_scene(scene), m_bus(bus), currentImageIndex(-1), isAutoNext(true) 
{
    autoTimer = new QTimer(this);
    connect(autoTimer, &QTimer::timeout, this, &ViewerService::onAutoTimerTimeout);
}

QString ViewerService::getCurrent2DImagePath() const { 
    return current2DImagePath; 
}

void ViewerService::setCurrent2DImagePath(const QString &path) { 
    current2DImagePath = path; 
}

void ViewerService::setImageList(const QStringList &list, int index) {
    imageFileList = list;
    currentImageIndex = index;
    emit m_bus->imageListUpdated(imageFileList, currentImageIndex);
    emit m_bus->imageIndexChanged(currentImageIndex, imageFileList.size());
}

void ViewerService::loadCurrentIndexImage() {
    if (currentImageIndex < 0 || currentImageIndex >= imageFileList.size()) return;
    current2DImagePath = QFileInfo(current2DImagePath).absolutePath() + "/" + imageFileList[currentImageIndex];
    
    // Always render the current image when navigating so buttons update the main screen
    m_scene->setTextureActor(Image2DLoader::load(current2DImagePath));
    m_scene->render();
    
    emit m_bus->imageIndexChanged(currentImageIndex, imageFileList.size());
}

void ViewerService::onNextImage() {
    if (currentImageIndex < imageFileList.size() - 1) {
        currentImageIndex++;
        loadCurrentIndexImage();
    }
}

void ViewerService::onPrevImage() {
    if (currentImageIndex > 0) {
        currentImageIndex--;
        loadCurrentIndexImage();
    }
}

void ViewerService::onAutoNext() {
    if (autoTimer->isActive() && isAutoNext) {
        autoTimer->stop();
    } else {
        isAutoNext = true;
        autoTimer->start(500);
    }
    emit m_bus->autoNavigationChanged(autoTimer->isActive(), isAutoNext);
}

void ViewerService::onAutoPrev() {
    if (autoTimer->isActive() && !isAutoNext) {
        autoTimer->stop();
    } else {
        isAutoNext = false;
        autoTimer->start(500);
    }
    emit m_bus->autoNavigationChanged(autoTimer->isActive(), isAutoNext);
}

AIMode ViewerService::getCurrentAIMode() const { 
    return currentAIMode; 
}

void ViewerService::setAIMode(AIMode mode) { 
    currentAIMode = mode; 
}

void ViewerService::onAutoTimerTimeout() {
    if (isAutoNext) {
        if (currentImageIndex < imageFileList.size() - 1) onNextImage();
        else autoTimer->stop();
    } else {
        if (currentImageIndex > 0) onPrevImage();
        else autoTimer->stop();
    }
    if (!autoTimer->isActive()) emit m_bus->autoNavigationChanged(false, isAutoNext);
}
