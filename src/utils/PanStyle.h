#ifndef PANSTYLE_H
#define PANSTYLE_H

#include <vtkInteractorStyleTrackballCamera.h>
#include <vtkObjectFactory.h>

class PanStyle : public vtkInteractorStyleTrackballCamera
{
public:
    static PanStyle* New();
    vtkTypeMacro(PanStyle, vtkInteractorStyleTrackballCamera);

    virtual void OnRightButtonDown() override
    {
        // Gọi StartPan() thay vì hành vi mặc định
        this->StartPan();
    }

    virtual void OnRightButtonUp() override
    {
        this->EndPan();
    }
};

#endif
