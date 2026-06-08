#!/bin/bash
set -e
git pull
cmake --build build
ctest --output-on-failure
git add .
git commit -m "Agent task completed"
git push
