#ifndef IMAGE2DLOADER_H
#define IMAGE2DLOADER_H

#include <QString>
#include <vtkSmartPointer.h>
#include <vtkActor.h>
#include <vtkImageData.h>
#include <vtkImageReader2.h>
#include <vtkPNGReader.h>
#include <vtkJPEGReader.h>
#include <vtkPlaneSource.h>
#include <vtkTexture.h>
#include <vtkPolyDataMapper.h>
#include <vtkProperty.h>

#include "Global.h"
class APP_EXPORT Image2DLoader {
public:
    static vtkSmartPointer<vtkActor> load(const QString& fileName);
};

#endif // IMAGE2DLOADER_H
