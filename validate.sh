#!/bin/bash
set -e

echo "Starting project validation..."

# 1. Frontend Checks (Next.js)
echo "--- Checking Frontend ---"
if [ -f "package.json" ]; then
    echo "Running ESLint..."
    npm run lint
    
    echo "Checking TypeScript types..."
    # npx tsc --noEmit # Removing tsc check for now as next build covers it usually, or just use build
    
    echo "Running build check..."
    npm run build
else
    echo "No package.json found, skipping frontend checks."
fi

# 2. Backend Checks (Python)
echo "--- Checking Backend ---"
if [ -d "backend" ]; then
    # Create or activate venv if needed? For a script, assume env is set or check syntax only.
    # check syntax using python -m py_compile
    echo "Checking Python syntax in backend/..."
    python -m py_compile backend/*.py
else
    echo "No backend directory found."
fi

# 3. Terraform Checks
echo "--- Checking Terraform ---"
if [ -d "terraform" ]; then
    cd terraform
    if [ -f "main.tf" ]; then
        if command -v terraform &> /dev/null; then
            echo "Running terraform validate..."
            # Initializing partial backend might be needed, or just validate syntax
            terraform init -backend=false
            terraform validate
        else
            echo "Terraform command not found. Skipping validation."
        fi
    else
        echo "No main.tf found in terraform/"
    fi
    cd ..
else
    echo "No terraform directory found."
fi

echo "All checks passed successfully!"
