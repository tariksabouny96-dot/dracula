"""
HOOD Domain Lead Agent Hierarchy & Registry
Governed by Master System Specification Section 4.1 & Build Instructions Section 12.

These legacy domain leads produce model *reasoning* only. A lead's output is an
analysis, never a verified outcome: it carries no success verdict, no fabricated
evidence, and is explicitly marked UNVERIFIED. Independent verification of
postconditions is the job of the multi-agent engine's checker
(services/agents/engine.py), not of these leads (finding F09).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from packages.contracts import (
    TaskNode,
    AgentResponseContract,
    EvidencePacket,
    ModelRequest,
    ModelClass
)
from services.model_gateway.router import ModelRouter


class BaseAgent(ABC):
    def __init__(self, name: str, domain: str, role_description: str, model_router: ModelRouter):
        self.name = name
        self.domain = domain
        self.role_description = role_description
        self.model_router = model_router

    @abstractmethod
    def execute(self, task: TaskNode) -> AgentResponseContract:
        pass

    def _reason(
        self,
        task: TaskNode,
        system_prompt: str,
        model_class: ModelClass = ModelClass.STANDARD
    ) -> AgentResponseContract:
        """Run the lead's model and return an honest, explicitly-unverified response.

        No lead fabricates a verdict. The model's text is reported as analysis;
        confidence stays modest; evidence is marked AI reasoning and non-primary;
        and simulated (mock/fallback) output is labelled as such so a caller can
        never mistake it for a live, verified result.
        """
        req = ModelRequest(
            model_class=model_class,
            prompt=f"Task: {task.title}\nObjective: {task.objective}\nInputs: {task.inputs}",
            system_prompt=system_prompt,
            task_id=task.task_id,
            agent=self.name,
        )
        resp = self.model_router.invoke(req)
        simulated = bool(getattr(resp, "is_mock", False) or getattr(resp, "is_fallback", False))

        result: Dict[str, Any] = {
            "analysis": resp.text,
            "verdict": "UNVERIFIED",
        }
        if simulated:
            result["simulated"] = True

        return AgentResponseContract(
            task_id=task.task_id,
            parent_task=task.parent_task_id,
            agent=self.name,
            objective=task.objective,
            inputs=task.inputs,
            context_used=["project_contracts", "system_config"],
            result=result,
            evidence=[EvidencePacket(
                claim=f"{self.domain} lead analysis (model reasoning; not independently verified)",
                source=f"Model Gateway ({resp.provider.value}/{resp.model_name})",
                source_type="ai_reasoning",
                is_primary=False,
                confidence=0.5,
                verifying_agent=self.name,
            )],
            assumptions=[],
            confidence=0.3 if simulated else 0.5,
            risks=[],
            unknowns=["Lead output is unverified until the independent checker confirms postconditions"],
            alternatives=[],
            recommendation=(resp.text or "").strip()[:200],
            artifacts=[],
            # Reasoning only: no verified tool was used, so the context bus must
            # classify this as derived inference, not verified-primary evidence.
            tools_used=[],
            cost=resp.usage.estimated_cost_usd,
            execution_time_ms=resp.latency_ms,
            followup=[],
        )


class EngineeringLeadAgent(BaseAgent):
    def __init__(self, model_router: ModelRouter):
        super().__init__(
            name="Engineering_Lead",
            domain="Engineering",
            role_description="Architecture, software, integrations, delivery, backend, frontend, DevOps, QA",
            model_router=model_router
        )

    def execute(self, task: TaskNode) -> AgentResponseContract:
        return self._reason(
            task,
            system_prompt=f"You are the HOOD {self.name}. Design robust, tested, portable code and architecture.",
        )


class CybersecurityLeadAgent(BaseAgent):
    def __init__(self, model_router: ModelRouter):
        super().__init__(
            name="Cybersecurity_Lead",
            domain="Cybersecurity",
            role_description="Independent security review, defense, AppSec, IAM, threat intel, vulnerability response",
            model_router=model_router
        )

    def execute(self, task: TaskNode) -> AgentResponseContract:
        # Never emits a PASS/FAIL verdict: a security opinion from a model is not
        # an audit. The result is advisory analysis only.
        return self._reason(
            task,
            system_prompt="You are the HOOD Cybersecurity Lead. Enforce least privilege, secret isolation, and sandboxing.",
        )


class CommerceLeadAgent(BaseAgent):
    def __init__(self, model_router: ModelRouter):
        super().__init__(
            name="Commerce_Lead",
            domain="Commerce",
            role_description="Commercial/e-commerce execution, pricing, supplier, marketing, economics",
            model_router=model_router
        )

    def execute(self, task: TaskNode) -> AgentResponseContract:
        return self._reason(
            task,
            system_prompt="You are the HOOD Commerce Lead. Analyse pricing, suppliers and commercial risk.",
        )


class ResearchLeadAgent(BaseAgent):
    def __init__(self, model_router: ModelRouter):
        super().__init__(
            name="Research_Lead",
            domain="Research",
            role_description="External intelligence, source verification, fact checking, competitive analysis",
            model_router=model_router
        )

    def execute(self, task: TaskNode) -> AgentResponseContract:
        return self._reason(
            task,
            system_prompt="You are the HOOD Research Lead. Summarise findings and flag unverified claims.",
        )


class DataLeadAgent(BaseAgent):
    def __init__(self, model_router: ModelRouter):
        super().__init__(
            name="Data_Lead",
            domain="Data",
            role_description="Data pipelines, analytics, data quality, knowledge engineering",
            model_router=model_router
        )

    def execute(self, task: TaskNode) -> AgentResponseContract:
        return self._reason(
            task,
            system_prompt="You are the HOOD Data Lead. Reason about pipelines, schema and data quality.",
        )


class OperationsLeadAgent(BaseAgent):
    def __init__(self, model_router: ModelRouter):
        super().__init__(
            name="Operations_Lead",
            domain="Operations",
            role_description="Reliability of Hood itself, scheduler, monitoring, cost controller, recovery",
            model_router=model_router
        )

    def execute(self, task: TaskNode) -> AgentResponseContract:
        # Does NOT assert "system health OPTIMAL": real telemetry comes from the
        # grounded diagnostics path in the commander, not from this reasoning lead.
        return self._reason(
            task,
            system_prompt="You are the HOOD Operations Lead. Reason about reliability, scheduling and recovery.",
        )


class LeadAgentRegistry:
    """Registry managing the six permanent domain leads and Hood lead agent."""

    def __init__(self, model_router: ModelRouter):
        self.model_router = model_router
        self._leads: Dict[str, BaseAgent] = {}
        self._register_default_leads()

    def _register_default_leads(self):
        self._leads["Engineering_Lead"] = EngineeringLeadAgent(self.model_router)
        self._leads["Cybersecurity_Lead"] = CybersecurityLeadAgent(self.model_router)
        self._leads["Commerce_Lead"] = CommerceLeadAgent(self.model_router)
        self._leads["Research_Lead"] = ResearchLeadAgent(self.model_router)
        self._leads["Data_Lead"] = DataLeadAgent(self.model_router)
        self._leads["Operations_Lead"] = OperationsLeadAgent(self.model_router)

    def get_lead(self, name_or_domain: str) -> Optional[BaseAgent]:
        for name, lead in self._leads.items():
            if name.lower() == name_or_domain.lower() or lead.domain.lower() == name_or_domain.lower():
                return lead
        return None

    def list_leads(self) -> List[dict]:
        return [
            {"name": l.name, "domain": l.domain, "description": l.role_description}
            for l in self._leads.values()
        ]
