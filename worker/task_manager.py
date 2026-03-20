"""
TaskManager implementation using multiprocessing.
Utilizes multiple CPU cores for concurrent quantum circuit execution.
"""
import json
import logging
import os
import uuid
from datetime import datetime
from multiprocessing import Process, Queue, Manager, cpu_count
from pathlib import Path
from typing import Dict, Any, Optional

from qiskit import qasm3
from qiskit.providers.aer import AerSimulator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Number of shots for quantum circuit execution
NUM_SHOTS = 1024

# Task storage file
TASK_STORAGE_FILE = Path("data/tasks.json")


def execute_quantum_circuit(qasm3_string: str) -> Dict[str, int]:
    """
    Execute a quantum circuit in a separate process.

    Args:
        qasm3_string: Serialized quantum circuit in QASM3 format
        
    Returns:
        Dictionary of measurement outcomes and counts
    """
    try:
        # Deserialize QASM3 string to QuantumCircuit
        qc = qasm3.loads(qasm3_string)
        
        # Validate that the circuit has measurements
        if not any(instr.operation.name == 'measure' for instr in qc.data):
            qc.measure_all()
        
        # Execute the quantum circuit
        simulator = AerSimulator()
        job = simulator.run(qc, shots=NUM_SHOTS)
        result = job.result()
        counts = result.get_counts()
        
        # Convert counts to ensure all keys are strings and values are ints
        return {str(k): int(v) for k, v in counts.items()}
    
    except Exception as e:
        logger.error(f"Error executing quantum circuit: {str(e)}")
        raise


def worker_process(task_queue: Queue, result_queue: Queue, worker_id: int):
    """
    Worker process that processes tasks from the queue.

    Args:
        task_queue: Queue containing task IDs to process
        result_queue: Queue for sending results back
        worker_id: Unique identifier for this worker
    """
    logger.info(f"Worker {worker_id} started (PID: {os.getpid()})")
    
    while True:
        try:
            # Get task from queue (blocking with timeout)
            task_data = task_queue.get(timeout=1)
            
            if task_data is None:
                logger.info(f"Worker {worker_id} received stop signal")
                break
            
            task_id, qasm3_string = task_data
            logger.info(f"Worker {worker_id} processing task {task_id}")
            
            try:
                # Execute quantum circuit
                result = execute_quantum_circuit(qasm3_string)
                
                # Send result back
                result_queue.put({
                    'task_id': task_id,
                    'status': 'completed',
                    'result': result,
                    'error': None
                })
                
                logger.info(f"Worker {worker_id} completed task {task_id}")
            
            except Exception as e:
                # Send error back
                error_msg = str(e) or repr(e) or type(e).__name__
                result_queue.put({
                    'task_id': task_id,
                    'status': 'error',
                    'result': None,
                    'error': error_msg
                })
                
                logger.error(f"Worker {worker_id} failed task {task_id}: {error_msg}")
        
        except Exception as e:
            if "Empty" not in str(type(e).__name__):
                logger.error(f"Worker {worker_id} error: {str(e)}")
            continue
    
    logger.info(f"Worker {worker_id} stopped")


class TaskManagerMultiprocessing:
    """
    Task manager using multiprocessing.
    Utilizes multiple CPU cores for concurrent quantum circuit simulation.
    """
    
    def __init__(self, num_workers: Optional[int] = None):
        """
        Initialize the task manager with worker processes.
        
        Args:
            num_workers: Number of worker processes (default: CPU count)
        """
        # Determine number of workers
        self.num_workers = num_workers or cpu_count()
        logger.info(f"Initializing TaskManager with {self.num_workers} worker processes")
        
        # Create multiprocessing Manager for shared state
        self.manager = Manager()
        self.tasks = self.manager.dict()
        
        # Create queues for task distribution and result collection
        self.task_queue = Queue()
        self.result_queue = Queue()
        
        # Worker processes
        self.workers = []
        self.running = False
        
        # Ensure data directory exists
        TASK_STORAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing tasks from file
        self._load_tasks()
        
        # Start worker processes
        self.start_workers()
        
        # Start result collector process
        self.result_collector = Process(target=self._collect_results, daemon=True)
        self.result_collector.start()
    
    def _load_tasks(self):
        """Load tasks from persistent storage."""
        if TASK_STORAGE_FILE.exists():
            try:
                with open(TASK_STORAGE_FILE, 'r') as f:
                    loaded_tasks = json.load(f)
                    # Copy to managed dict
                    for task_id, task_data in loaded_tasks.items():
                        self.tasks[task_id] = task_data
                logger.info(f"Loaded {len(self.tasks)} tasks from storage")
            except Exception as e:
                logger.error(f"Error loading tasks: {e}")
    
    def _save_tasks(self):
        """Save tasks to persistent storage with atomic write."""
        temp_file = TASK_STORAGE_FILE.with_suffix('.tmp')
        
        try:
            # Write to temporary file first
            tasks_dict = dict(self.tasks)
            with open(temp_file, 'w') as f:
                json.dump(tasks_dict, f, indent=2)
            
            # Atomic rename (POSIX guarantees atomicity)
            # If crash occurs before this, old file remains intact
            temp_file.replace(TASK_STORAGE_FILE)
            
        except Exception as e:
            logger.error(f"Error saving tasks: {e}")
            # Clean up temp file if it exists
            if temp_file.exists():
                temp_file.unlink()
    
    def _collect_results(self):
        """
        Background process that collects results from workers.
        Updates task status and saves to disk.
        """
        logger.info("Result collector started")
        
        while True:
            try:
                # Get result from queue (blocking with timeout)
                result_data = self.result_queue.get(timeout=1)
                
                task_id = result_data['task_id']
                
                # Update task in shared dict
                if task_id in self.tasks:
                    task = dict(self.tasks[task_id])
                    task['status'] = result_data['status']
                    task['result'] = result_data['result']
                    task['error'] = result_data['error']
                    task['updated_at'] = datetime.utcnow().isoformat()
                    self.tasks[task_id] = task
                    
                    # Save to disk
                    self._save_tasks()
                    
                    logger.info(f"Updated task {task_id} status: {result_data['status']}")
            
            except Exception as e:
                if "Empty" not in str(type(e).__name__):
                    logger.error(f"Result collector error: {str(e)}")
                continue
    
    def submit_task(self, qasm3_string: str) -> str:
        """
        Submit a new task for processing.
        
        Args:
            qasm3_string: Serialized quantum circuit in QASM3 format
            
        Returns:
            task_id: Unique identifier for the task
        """
        task_id = str(uuid.uuid4())
        
        # Create task entry
        task_data = {
            "status": "pending",
            "qasm3_string": qasm3_string,
            "result": None,
            "error": None,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        # Store in shared dict
        self.tasks[task_id] = task_data
        self._save_tasks()
        
        # Add to processing queue
        self.task_queue.put((task_id, qasm3_string))
        logger.info(f"Task {task_id} submitted to queue")
        
        return task_id
    
    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get task status and result.
        
        Args:
            task_id: Unique identifier for the task
            
        Returns:
            Task information or None if not found
        """
        return dict(self.tasks.get(task_id)) if task_id in self.tasks else None
    
    def start_workers(self):
        """Start worker processes."""
        if not self.running:
            self.running = True
            
            for i in range(self.num_workers):
                worker = Process(
                    target=worker_process,
                    args=(self.task_queue, self.result_queue, i),
                    daemon=True
                )
                worker.start()
                self.workers.append(worker)
            
            logger.info(f"Started {self.num_workers} worker processes")
    
    def stop_workers(self):
        """Stop worker processes gracefully."""
        if self.running:
            logger.info("Stopping worker processes...")
            
            for _ in range(self.num_workers):
                self.task_queue.put(None)
            
            # Wait for workers to finish
            for worker in self.workers:
                worker.join(timeout=5)
                if worker.is_alive():
                    worker.terminate()
            
            # Stop result collector
            if self.result_collector.is_alive():
                self.result_collector.terminate()
            
            self.running = False
            logger.info("All workers stopped")


# Global task manager instance
task_manager = None


def get_task_manager(num_workers: Optional[int] = None) -> TaskManagerMultiprocessing:
    """
    Get or create the global task manager instance.
    
    Args:
        num_workers: Number of worker processes (default: CPU count)
        
    Returns:
        TaskManagerMultiprocessing instance
    """
    global task_manager
    if task_manager is None:
        task_manager = TaskManagerMultiprocessing(num_workers=num_workers)
    return task_manager
