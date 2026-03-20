"""
Integration tests for the Quantum Circuit API.
Tests task submission, processing, and result retrieval.
"""
import pytest
import time
import httpx
from qiskit import QuantumCircuit
from qiskit import qasm3

# API base URL - adjust if needed
API_BASE_URL = "http://localhost:8000"


@pytest.fixture
def api_client():
    """Create an HTTP client for API testing."""
    return httpx.Client(base_url=API_BASE_URL, timeout=30.0)


def create_test_quantum_circuit() -> str:
    """
    Create a test quantum circuit and serialize it to QASM3.
    
    Returns:
        QASM3 string representation of the circuit
    """
    qc = QuantumCircuit(2, 2)
    qc.h(0)  # Hadamard on qubit 0
    qc.cx(0, 1)  # CNOT from qubit 0 to qubit 1
    qc.measure([0, 1], [0, 1])  # Measure both qubits
    return qasm3.dumps(qc)


def test_root_endpoint(api_client):
    """Test the root endpoint returns API information."""
    response = api_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert "version" in data
    assert "endpoints" in data


def test_health_check(api_client):
    """Test the health check endpoint."""
    response = api_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


def test_submit_task_success(api_client):
    """Test successful task submission."""
    qasm3_string = create_test_quantum_circuit()
    
    response = api_client.post(
        "/tasks",
        json={"qc": qasm3_string}
    )
    
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert "message" in data
    assert data["message"] == "Task submitted successfully."
    assert len(data["task_id"]) > 0


def test_submit_task_empty_circuit(api_client):
    """Test task submission with empty circuit string."""
    response = api_client.post(
        "/tasks",
        json={"qc": ""}
    )
    
    # Should return 422 validation error (Pydantic validation)
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_submit_task_invalid_json(api_client):
    """Test task submission with invalid JSON."""
    response = api_client.post(
        "/tasks",
        json={"invalid_key": "value"}
    )
    
    assert response.status_code == 422  # Validation error


def test_submit_task_invalid_qasm_syntax(api_client):
    """Test task submission with invalid QASM syntax."""
    invalid_qasm = """
    OPENQASM 3.0;
    include "stdgates.inc";
    qubit[2] q;
    bit[2] c;
    h q[0];
    invalid_gate q[1];  // This gate doesn't exist
    c = measure q;
    """
    
    response = api_client.post(
        "/tasks",
        json={"qc": invalid_qasm}
    )
    
    # Should return 422 validation error due to invalid QASM
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_submit_task_not_qasm_format(api_client):
    """Test task submission with non-QASM string."""
    response = api_client.post(
        "/tasks",
        json={"qc": "This is just plain text, not QASM"}
    )
    
    # Should return 422 validation error
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_submit_task_malformed_qasm(api_client):
    """Test task submission with malformed QASM structure."""
    malformed_qasm = """
    OPENQASM 3.0;
    qubit[2] q;
    h q[0]  // Missing semicolon
    """
    
    response = api_client.post(
        "/tasks",
        json={"qc": malformed_qasm}
    )
    
    # Should return 422 validation error
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_submit_task_whitespace_only(api_client):
    """Test task submission with whitespace-only string."""
    response = api_client.post(
        "/tasks",
        json={"qc": "   \n\t  "}
    )
    
    # Should return 422 validation error
    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


def test_submit_task_valid_minimal_qasm(api_client):
    """Test task submission with minimal valid QASM circuit."""
    minimal_qasm = """
    OPENQASM 3.0;
    include "stdgates.inc";
    qubit[1] q;
    bit[1] c;
    h q[0];
    c[0] = measure q[0];
    """
    
    response = api_client.post(
        "/tasks",
        json={"qc": minimal_qasm}
    )
    
    # Should succeed
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert "message" in data


def test_get_task_not_found(api_client):
    """Test retrieving a non-existent task."""
    response = api_client.get("/tasks/nonexistent-task-id")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "not found" in data["message"].lower()


def test_complete_workflow(api_client):
    """
    Test the complete workflow: submit task, wait for completion, retrieve result.
    This is the main integration test.
    """
    # Step 1: Submit a quantum circuit task
    qasm3_string = create_test_quantum_circuit()
    
    submit_response = api_client.post(
        "/tasks",
        json={"qc": qasm3_string}
    )
    
    assert submit_response.status_code == 202
    submit_data = submit_response.json()
    task_id = submit_data["task_id"]
    
    # Step 2: Poll for task completion (with timeout)
    max_attempts = 30  # 30 seconds timeout
    attempt = 0
    task_completed = False
    
    while attempt < max_attempts:
        time.sleep(1)
        
        get_response = api_client.get(f"/tasks/{task_id}")
        assert get_response.status_code == 200
        
        get_data = get_response.json()
        status = get_data["status"]
        
        if status == "completed":
            task_completed = True
            # Step 3: Verify the result
            assert "result" in get_data
            result = get_data["result"]
            
            # Verify result structure
            assert isinstance(result, dict)
            assert len(result) > 0
            
            # For a Bell state, we expect roughly equal distribution of "00" and "11"
            # (allowing for quantum randomness)
            total_shots = sum(result.values())
            assert total_shots > 0
            
            # Check that we have valid measurement outcomes
            for key in result.keys():
                assert all(c in '01' for c in key)
            
            break
        elif status == "error":
            pytest.fail(f"Task failed with error: {get_data.get('message', 'Unknown error')}")
        elif status == "pending":
            # Task still processing, continue polling
            attempt += 1
        else:
            pytest.fail(f"Unexpected task status: {status}")
    
    assert task_completed, f"Task did not complete within {max_attempts} seconds"


def test_multiple_tasks_concurrently(api_client):
    """Test submitting multiple tasks and verifying they all complete."""
    qasm3_string = create_test_quantum_circuit()
    
    # Submit multiple tasks
    num_tasks = 3
    task_ids = []
    
    for _ in range(num_tasks):
        response = api_client.post(
            "/tasks",
            json={"qc": qasm3_string}
        )
        assert response.status_code == 202
        task_ids.append(response.json()["task_id"])
    
    # Wait for all tasks to complete
    max_attempts = 60  # 60 seconds timeout for multiple tasks
    completed_tasks = set()
    
    for attempt in range(max_attempts):
        time.sleep(1)
        
        for task_id in task_ids:
            if task_id in completed_tasks:
                continue
                
            response = api_client.get(f"/tasks/{task_id}")
            assert response.status_code == 200
            
            data = response.json()
            if data["status"] == "completed":
                completed_tasks.add(task_id)
                assert "result" in data
            elif data["status"] == "error":
                pytest.fail(f"Task {task_id} failed: {data.get('message')}")
        
        if len(completed_tasks) == num_tasks:
            break
    
    assert len(completed_tasks) == num_tasks, \
        f"Only {len(completed_tasks)}/{num_tasks} tasks completed"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
