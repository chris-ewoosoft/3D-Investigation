#include "Model3DLoader.h"
#include <QFileInfo>
#include <QDebug>

std::vector<vtkSmartPointer<vtkActor>> Model3DLoader::load(const QString& objPath, const QString& mtlPath) {
    std::vector<vtkSmartPointer<vtkActor>> actorsList;
    QFileInfo objFile(objPath);
    QFileInfo mtlFile(mtlPath);

    if (!objFile.exists()) {
        qWarning() << "OBJ file not found:" << objPath;
        return actorsList;
    }

    bool imported = false;
    if (mtlFile.exists()) {
        vtkNew<vtkOBJImporter> importer;
        importer->SetFileName(objPath.toStdString().c_str());
        importer->SetFileNameMTL(mtlPath.toStdString().c_str());
        importer->Update();

        vtkRenderer *importerRenderer = importer->GetRenderer();
        if (importerRenderer) {
            vtkActorCollection *actors = importerRenderer->GetActors();
            actors->InitTraversal();
            vtkActor *actor;
            while ((actor = actors->GetNextActor())) {
                actor->GetProperty()->SetLighting(true);
                actor->GetProperty()->SetInterpolationToPhong();
                actor->GetProperty()->SetAmbient(0.3);
                actor->GetProperty()->SetDiffuse(0.8);
                actorsList.push_back(actor);
            }
            imported = true;
        }
    }

    if (!imported) {
        vtkNew<vtkOBJReader> reader;
        reader->SetFileName(objPath.toStdString().c_str());
        reader->Update();
        
        vtkNew<vtkPolyDataMapper> mapper;
        mapper->SetInputConnection(reader->GetOutputPort());
        
        vtkSmartPointer<vtkActor> fallbackActor = vtkSmartPointer<vtkActor>::New();
        fallbackActor->SetMapper(mapper);
        fallbackActor->GetProperty()->SetColor(0.7, 0.7, 0.7);
        actorsList.push_back(fallbackActor);
    }

    return actorsList;
}
