"""
FastAPI application for Quantum Circuit API.
Handles task submission and retrieval endpoints.
"""
import logging
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from worker.task_manager import get_task_manager

# Initialize multiprocessing task manager
task_manager = get_task_manager()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Quantum Circuit API",
    description="API for executing Quantum Circuits asynchronously",
    version="1.0.0"
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom exception handler for validation errors.
    Logs validation failures with details about what failed.
    """
    errors = exc.errors()
    error_details = []
    
    for error in errors:
        field = ".".join(str(loc) for loc in error["loc"])
        msg = error["msg"]
        error_type = error.get("type", "unknown")
        error_details.append(f"{field}: {msg}")
        
        # Log each validation error
        logger.warning(
            f"Validation failed for request to {request.url.path} - "
            f"Field: {field}, Type: {error_type}, Error: {msg}"
        )
    
    # Convert errors to JSON-serializable format
    serializable_errors = []
    for error in errors:
        serializable_error = {
            "loc": error["loc"],
            "msg": error["msg"],
            "type": error.get("type", "unknown")
        }
        if "ctx" in error:
            # Convert context to string if present
            serializable_error["ctx"] = {k: str(v) for k, v in error["ctx"].items()}
        serializable_errors.append(serializable_error)
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": serializable_errors
        }
    )


class QuantumCircuitRequest(BaseModel):
    """Request model for quantum circuit submission."""
    qc: str

    @field_validator('qc')
    @classmethod
    def validate_qc(cls, v: str) -> str:
        """Validate the quantum circuit string by attempting to parse it."""
        # Get logger for validation
        validation_logger = logging.getLogger(__name__)
        
        if not v or not v.strip():
            validation_logger.warning("QASM validation failed: Empty or whitespace-only quantum circuit")
            raise ValueError("Quantum circuit cannot be empty")
        
        v_stripped = v.strip()
        
        # Basic format check
        if not v_stripped.startswith("OPENQASM"):
            validation_logger.warning(
                f"QASM validation failed: Circuit does not start with 'OPENQASM'. "
                f"Received: {v_stripped[:50]}..."
            )
            raise ValueError("Quantum circuit must be in QASM format (should start with 'OPENQASM')")
        
        # Try to parse the QASM to validate syntax
        try:
            from qiskit import qasm3
            qasm3.loads(v_stripped)
        except Exception as e:
            error_msg = str(e) or repr(e) or type(e).__name__
            validation_logger.warning(
                f"QASM validation failed: Invalid QASM syntax - {error_msg}"
            )
            raise ValueError(f"Invalid QASM syntax: {error_msg}")
        
        return v


class TaskSubmissionResponse(BaseModel):
    """Response model for task submission."""
    task_id: str
    message: str


class TaskResultResponse(BaseModel):
    """Response model for task result retrieval."""
    status: str
    result: Optional[Dict[str, int]] = None
    message: Optional[str] = None


@app.get("/")
async def root():
    """Root endpoint providing API information."""
    return {
        "name": "Quantum Circuit API",
        "version": "1.0.0",
        "endpoints": {
            "submit_task": "POST /tasks",
            "get_task": "GET /tasks/{id}"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/tasks", response_model=TaskSubmissionResponse, status_code=202)
async def submit_task(request: QuantumCircuitRequest):
    """
    Submit a quantum circuit for asynchronous processing.
    
    Args:
        request: QuantumCircuitRequest containing the serialized quantum circuit
        
    Returns:
        TaskSubmissionResponse with task_id and confirmation message
    """
    try:
        logger.info("Received quantum circuit submission request")
        
        # Validate that qc is not empty
        if not request.qc or not request.qc.strip():
            raise HTTPException(
                status_code=400,
                detail="Quantum circuit (qc) cannot be empty"
            )
        
        # Submit task to task manager
        task_id = task_manager.submit_task(request.qc)
        
        logger.info(f"Task submitted successfully with ID: {task_id}")
        
        return TaskSubmissionResponse(
            task_id=task_id,
            message="Task submitted successfully."
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error submitting task: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to submit task: {str(e)}"
        )


@app.get("/tasks/{task_id}", response_model=TaskResultResponse)
async def get_task(task_id: str):
    """
    Retrieve the status and result of a quantum circuit task.
    
    Args:
        task_id: Unique identifier for the task
        
    Returns:
        TaskResultResponse with status and result (if completed)
    """
    try:
        logger.info(f"Retrieving task status for ID: {task_id}")
        
        # Get task from task manager
        task = task_manager.get_task(task_id)
        
        if not task:
            logger.warning(f"Task {task_id} not found")
            return TaskResultResponse(
                status="error",
                message="Task not found."
            )
        
        status = task["status"]
        
        if status == "completed":
            logger.info(f"Task {task_id} completed successfully")
            return TaskResultResponse(
                status="completed",
                result=task["result"]
            )
        
        elif status == "error":
            error_msg = task.get("error", "Unknown error")
            logger.error(f"Task {task_id} failed: {error_msg}")
            return TaskResultResponse(
                status="error",
                message=f"Task failed: {error_msg}"
            )
        
        else:
            # Task is pending or processing
            logger.info(f"Task {task_id} is still in progress (status: {status})")
            return TaskResultResponse(
                status="pending",
                message="Task is still in progress."
            )
            
    except Exception as e:
        logger.error(f"Error retrieving task {task_id}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve task: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
