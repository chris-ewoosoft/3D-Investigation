git pull

# Build project
cmake --build build --config Release
if ($LASTEXITCODE -ne 0) { 
    Write-Error "Build failed"
    exit 1 
}

# Run tests
ctest --output-on-failure
if ($LASTEXITCODE -ne 0) { 
    Write-Error "Tests failed"
    exit 1 
}

# Commit and push
git add .
git commit -m "Agent task completed"
git push
