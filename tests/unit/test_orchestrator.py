import pytest
from packages.contracts import TaskNode, TaskStatus, RiskLevel, AgentResponseContract
from services.orchestrator.dag_scheduler import DAGOrchestrator, CyclicDependencyError

def test_dag_cycle_detection():
    orchestrator = DAGOrchestrator()
    t1 = TaskNode(title="Task 1", objective="Do 1", dependencies=["t2"])
    t1.task_id = "t1"
    t2 = TaskNode(title="Task 2", objective="Do 2", dependencies=["t1"])
    t2.task_id = "t2"

    orchestrator.add_task(t1)
    orchestrator.add_task(t2)

    with pytest.raises(CyclicDependencyError):
        orchestrator.validate_dag()

def test_dag_dependency_execution():
    orchestrator = DAGOrchestrator()
    t1 = TaskNode(title="Prep", objective="Prepare env", risk_level=RiskLevel.L1)
    t1.task_id = "t1"
    t2 = TaskNode(title="Build", objective="Build artifact", dependencies=["t1"], risk_level=RiskLevel.L1)
    t2.task_id = "t2"

    orchestrator.add_task(t1)
    orchestrator.add_task(t2)

    executed_order = []
    def mock_executor(task: TaskNode) -> AgentResponseContract:
        executed_order.append(task.task_id)
        return AgentResponseContract(
            task_id=task.task_id,
            agent="TestAgent",
            objective=task.objective,
            result="Success"
        )

    results = orchestrator.run_dag(mock_executor)
    assert executed_order == ["t1", "t2"]
    assert results["t1"].status == TaskStatus.COMPLETED
    assert results["t2"].status == TaskStatus.COMPLETED
