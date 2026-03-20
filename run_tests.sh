#!/bin/bash

# Script to run integration tests with the API running
# Prerequisites: Test dependencies must be installed (see README.md)
set -e

echo "Starting API..."
docker-compose up -d

echo "Waiting for API to be ready..."
sleep 5

# Check if API is healthy
MAX_RETRIES=10
RETRY_COUNT=0
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo "API is ready!"
        break
    fi
    echo "Waiting for API... (attempt $((RETRY_COUNT + 1))/$MAX_RETRIES)"
    sleep 2
    RETRY_COUNT=$((RETRY_COUNT + 1))
done

if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
    echo "ERROR: API failed to start"
    docker-compose logs
    docker-compose down
    exit 1
fi

echo ""
echo "Running tests..."
pytest tests/ -v --tb=short

TEST_EXIT_CODE=$?

echo ""
echo "Stopping API..."
docker-compose down

if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo ""
    echo "All tests passed"
else
    echo ""
    echo "Some tests failed"
fi

exit $TEST_EXIT_CODE
