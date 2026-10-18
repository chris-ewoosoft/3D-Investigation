#ifndef CROSSHAIR_GEOMETRY_H
#define CROSSHAIR_GEOMETRY_H

// Orientation indices — canonical definition shared by CrosshairManager
// and CrosshairGeometry. Sống ở header pure-math này để CrosshairGeometry
// không phải kéo theo VTK (CrosshairManager.h).
inline constexpr int ORI_SAGITTAL = 0;
inline constexpr int ORI_CORONAL  = 1;
inline constexpr int ORI_AXIAL    = 2;

namespace CrosshairGeometry {

const double *resliceAxes(int orientation);
const double *lineColor(int orientation);
void labels(int orientation, const char *out[4]);
void worldToNormalized(int orientation, const double world[3], const double bounds[6], double &nx, double &ny);
void normalizedToWorld(int orientation, double nx, double ny, const double bounds[6],
                       const double oldWorld[3], double newWorld[3]);

}

#endif // CROSSHAIR_GEOMETRY_H
