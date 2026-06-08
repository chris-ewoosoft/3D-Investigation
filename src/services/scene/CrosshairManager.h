#ifndef CROSSHAIRMANAGER_H
#define CROSSHAIRMANAGER_H

// ============================================================
// CrosshairManager.h
// Crosshair overlay for 3 MPR views (Sagittal / Coronal / Axial).
//
// Features:
//   1. Two coloured lines per view cross at world-space m_center.
//   2. Anatomical labels (R/L, A/P, S/I) at the four line tips.
//   3. A small yellow sphere anchor at the crossing point.
//   4. Dragging the anchor updates m_center and re-slices the
//      other two views; the dragged view only moves its crosshair.
// ============================================================

#include <QObject>
#include <array>
#include <cmath>
#include <algorithm>

#include <vtkSmartPointer.h>
#include <vtkNew.h>
#include <vtkRenderer.h>
#include <vtkRenderWindow.h>
#include <vtkRenderWindowInteractor.h>
#include <vtkImageData.h>
#include <vtkImageReslice.h>
#include <vtkMatrix4x4.h>
#include <vtkImageActor.h>
#include <vtkImageProperty.h>
#include <vtkActor2D.h>
#include <vtkPolyDataMapper2D.h>
#include <vtkLineSource.h>
#include <vtkCoordinate.h>
#include <vtkProperty2D.h>
#include <vtkTextActor.h>
#include <vtkTextProperty.h>
#include <vtkSphereSource.h>
#include <vtkPolyDataMapper.h>
#include <vtkActor.h>
#include <vtkProperty.h>
#include <vtkInteractorStyleImage.h>
#include <vtkObjectFactory.h>
#include <vtkCamera.h>

// Orientation indices
static const int ORI_SAGITTAL = 0;
static const int ORI_CORONAL  = 1;
static const int ORI_AXIAL    = 2;

// ─── Per-view state ───────────────────────────────────────────────────────────
struct CrosshairOverlay {
    // Horizontal crosshair line
    vtkSmartPointer<vtkLineSource>       hLine;
    vtkSmartPointer<vtkPolyDataMapper2D> hMapper;
    vtkSmartPointer<vtkActor2D>          hActor;

    // Vertical crosshair line
    vtkSmartPointer<vtkLineSource>       vLine;
    vtkSmartPointer<vtkPolyDataMapper2D> vMapper;
    vtkSmartPointer<vtkActor2D>          vActor;

    // 4 anatomical labels: [negH, posH, posV, negV]
    vtkSmartPointer<vtkTextActor> labels[4];

    // Anchor sphere (3-D actor in the 2-D image plane)
    vtkSmartPointer<vtkSphereSource>   anchorSource;
    vtkSmartPointer<vtkPolyDataMapper> anchorMapper;
    vtkSmartPointer<vtkActor>          anchorActor;

    // MPR reslice pipeline
    vtkSmartPointer<vtkImageReslice>   reslice;
    vtkSmartPointer<vtkImageActor>     imageActor;

    int  orientation = -1;
    bool dragging    = false;
};

// ─── CrosshairManager ─────────────────────────────────────────────────────────
class CrosshairManager : public QObject
{
    Q_OBJECT
public:
    explicit CrosshairManager(QObject *parent = nullptr);
    ~CrosshairManager();

    // Call once after a DICOM volume is loaded.
    // renderers: [0]=Sagittal, [1]=Coronal, [2]=Axial
    void initialize(vtkImageData*    volume,
                    vtkRenderer*     sagittalRenderer,
                    vtkRenderer*     coronalRenderer,
                    vtkRenderer*     axialRenderer,
                    vtkRenderWindow* renderWindow,
                    double window = -1.0, double level = -1.0);

    // Remove all VTK actors from renderers
    void cleanup();

    // Called by CrosshairInteractorStyle
    void onLeftButtonDown(int vi, int x, int y);
    void onMouseMove     (int vi, int x, int y);
    void onLeftButtonUp  (int vi);

    // Accessors for the interactor style
    vtkRenderer* renderer(int i)   const { return (i>=0&&i<3)?m_renderers[i]:nullptr; }
    bool isDragging(int i)          const { return (i>=0&&i<3)?m_views[i].dragging:false; }
    bool anyDragging()              const {
        return m_views[0].dragging || m_views[1].dragging || m_views[2].dragging;
    }

    // Made public so CrosshairInteractorStyle can inspect .dragging
    CrosshairOverlay m_views[3];

private:
    double           m_center[3]    = {0, 0, 0};
    vtkImageData*    m_volume       = nullptr;
    vtkRenderWindow* m_renderWindow = nullptr;
    double           m_window = 1, m_level = 0;
    vtkRenderer*     m_renderers[3] = {nullptr, nullptr, nullptr};

    void buildOverlay  (int vi);
    void updateAllViews();
    void updateOverlay (int vi);
    void updateReslice (int vi);

    bool displayToWorld(int vi, int x, int y, double worldPt[3]);
    bool isNearAnchor  (int vi, int x, int y);

    static void getLabels(int orientation, const char* out[4]);
};

// ─── Interactor style ─────────────────────────────────────────────────────────
// One instance shared by the render window.
// Automatically detects which of the 3 MPR viewports the mouse is in.
class CrosshairInteractorStyle : public vtkInteractorStyleImage
{
public:
    static CrosshairInteractorStyle* New();
    vtkTypeMacro(CrosshairInteractorStyle, vtkInteractorStyleImage);

    CrosshairManager* manager    = nullptr;

    // Set this to the 3D renderer so left-drag rotates the 3D view.
    vtkRenderer*      renderer3D = nullptr;

    void OnLeftButtonDown()  override;
    void OnMouseMove()       override;
    void OnLeftButtonUp()    override;
    void OnRightButtonDown() override;
    void OnRightButtonUp()   override;
    void OnMiddleButtonDown()override;
    void OnMiddleButtonUp()  override;

private:
    int  detectView()   const; // returns ORI_* of the hovered viewport, or -1
    bool isIn3DView(int x, int y) const; // true when cursor is in the 3D viewport

    int  m_activeView  = -1;   // MPR view being crosshair-dragged (sticky)
    bool m_rotating3D  = false;// true while left-dragging in the 3D viewport
    int  m_lastX       = 0;
    int  m_lastY       = 0;
};

#endif // CROSSHAIRMANAGER_H
