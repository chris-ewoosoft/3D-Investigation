#ifndef FILE_CONSTANTS_H
#define FILE_CONSTANTS_H
#include <QString>
namespace AppConstants { namespace FileFilter {
    inline QString images()     { return QStringLiteral("Images (*.png *.jpg *.jpeg *.bmp)"); }
    inline QString objFiles()   { return QStringLiteral("3D Models (*.obj *.stl *.ply *.vtk *.vtp)"); }
    inline QString allFiles()   { return QStringLiteral("All Files (*.*)"); }
} }
#endif
