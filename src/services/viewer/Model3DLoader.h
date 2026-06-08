#ifndef MODEL3DLOADER_H
#define MODEL3DLOADER_H

#include <QString>
#include <vtkSmartPointer.h>
#include <vtkActor.h>
#include <vtkActorCollection.h>
#include <vtkProperty.h>
#include <vtkOBJImporter.h>
#include <vtkOBJReader.h>
#include <vtkPolyDataMapper.h>
#include <vtkRenderer.h>
#include <vector>

#include "Global.h"
class APP_EXPORT Model3DLoader {
public:
    static std::vector<vtkSmartPointer<vtkActor>> load(const QString& objPath, const QString& mtlPath);
};

#endif // MODEL3DLOADER_H
