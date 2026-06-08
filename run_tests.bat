@echo off
set "PATH=C:\Qt\6.9.3\msvc2022_64\bin;C:\opencv\build\x64\vc16\bin;%PATH%"

cd build
echo Đang chạy các Unit Test bằng CTest...
ctest -C Release --output-on-failure
pause
