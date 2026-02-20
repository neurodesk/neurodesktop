#!/bin/bash
# Build the neurodesktop-base image for testing
set -e

docker build -f Dockerfile.base -t neurodesktop-base:latest .

echo ""
echo "Build complete. Test with:"
echo "  docker run -it --rm neurodesktop-base:latest bash"
echo ""
echo "Quick validation:"
echo "  docker run --rm neurodesktop-base:latest python -c 'import nipype; print(nipype.__version__)'"
echo "  docker run --rm neurodesktop-base:latest which nextflow"
echo "  docker run --rm neurodesktop-base:latest jupyter nbconvert --version"
