"""
Pytest configuration and fixtures for integration tests.
"""
import pytest
import time
import httpx


def wait_for_api(base_url: str, timeout: int = 30) -> bool:
    """
    Wait for the API to become available.
    
    Args:
        base_url: Base URL of the API
        timeout: Maximum time to wait in seconds
        
    Returns:
        True if API is available, False otherwise
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        try:
            response = httpx.get(f"{base_url}/health", timeout=5.0)
            if response.status_code == 200:
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            time.sleep(1)
    
    return False


@pytest.fixture(scope="session", autouse=True)
def ensure_api_ready():
    """Ensure the API is ready before running tests."""
    api_url = "http://localhost:8000"
    
    if not wait_for_api(api_url):
        pytest.skip("API is not available")
