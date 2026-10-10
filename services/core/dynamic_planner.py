"""
HOOD Dynamic Context-Aware Plan Generator & Result Synthesizer
Generates intent-aware task graphs and rich human-facing synthesis.
Governed by Master System Specification Sections 2, 4, 10 & Live Operational Directive.
"""

from __future__ import annotations
from typing import List, Dict, Any, Optional
from packages.contracts import (
    TaskNode,
    TaskStatus,
    RiskLevel,
    AgentResponseContract,
    EvidencePacket
)
from packages.config import SystemConfig
from services.core.objective_analyzer import ObjectiveAnalyzer, ParsedObjective, ObjectiveCategory
from services.core.system_diagnostics import SystemDiagnosticsCollector
from services.core.shared_context_bus import SharedContextBus, ContextPacket, PacketType, EvidenceQuality


class DynamicPlanGenerator:
    """Builds customized, domain-routed, non-generic task graphs respecting user constraints."""

    @classmethod
    def generate_plan(cls, objective_text: str, project: str = "default", config: Optional[SystemConfig] = None) -> tuple[ParsedObjective, List[TaskNode]]:
        parsed = ObjectiveAnalyzer.analyze(objective_text)
        tasks: List[TaskNode] = []

        # CATEGORY 1: SYSTEM SELF-ASSESSMENT & DIAGNOSTIC
        if parsed.category == ObjectiveCategory.SYSTEM_DIAGNOSTIC:
            t1 = TaskNode(
                title="Inspect Host Environment, Hardware & Diagnostics",
                objective="Collect grounded hardware, OS, CPU, RAM, disk, and runtime telemetry",
                project=project,
                assigned_agent="Operations_Lead",
                risk_level=RiskLevel.L0,
                inputs={"diagnostics_mode": "hardware_runtime"}
            )
            t2 = TaskNode(
                title="Inspect Providers, Capabilities & Tool Registries",
                objective="Inspect active AI model providers, tool availability, and multi-node state",
                project=project,
                assigned_agent="Engineering_Lead",
                dependencies=[t1.task_id],
                risk_level=RiskLevel.L0,
                inputs={"diagnostics_mode": "providers_tools"}
            )
            t3 = TaskNode(
                title="Synthesize Verified System Status & Zero-Cost Improvements",
                objective="Assemble verified status report and identify top 3 zero-cost value improvements without modifying system",
                project=project,
                assigned_agent="Operations_Lead",
                dependencies=[t2.task_id],
                risk_level=RiskLevel.L1,
                inputs={"diagnostics_mode": "synthesis", "read_only": True}
            )
            tasks = [t1, t2, t3]

        # CATEGORY 2: RESEARCH & EVIDENCE GATHERING
        elif parsed.category == ObjectiveCategory.RESEARCH:
            t1 = TaskNode(
                title="Retrieve External Intelligence & Primary Sources",
                objective=f"Gather verified sources and facts for: {objective_text}",
                project=project,
                assigned_agent="Research_Lead",
                risk_level=RiskLevel.L0,
                inputs={"topic": objective_text}
            )
            t2 = TaskNode(
                title="Cross-Verify Claims & Separate Uncertainties",
                objective="Evaluate source credibility, detect conflicts, and calculate confidence",
                project=project,
                assigned_agent="Data_Lead",
                dependencies=[t1.task_id],
                risk_level=RiskLevel.L0
            )
            t3 = TaskNode(
                title="Assemble Evidence-Backed Synthesis & Recommendations",
                objective="Synthesize factual consensus, document unknowns, and recommend next step",
                project=project,
                assigned_agent="Research_Lead",
                dependencies=[t2.task_id],
                risk_level=RiskLevel.L1
            )
            tasks = [t1, t2, t3]

        # CATEGORY 3: COMMERCE & BUSINESS OPPORTUNITY
        elif parsed.category == ObjectiveCategory.COMMERCE_BUSINESS:
            t1 = TaskNode(
                title="Analyze Market Demand & Competitive Landscape",
                objective=f"Research market opportunity and competitors for: {objective_text}",
                project=project,
                assigned_agent="Research_Lead",
                risk_level=RiskLevel.L0
            )
            t2 = TaskNode(
                title="Evaluate Unit Economics & Pricing Feasibility",
                objective="Analyze margins, supplier structures, customer acquisition, and unit economics",
                project=project,
                assigned_agent="Commerce_Lead",
                dependencies=[t1.task_id],
                risk_level=RiskLevel.L1
            )
            t3 = TaskNode(
                title="Assess Technical Feasibility & Commercial Recommendation",
                objective="Synthesize technical requirements, delivery risks, and commercial go/no-go recommendation",
                project=project,
                assigned_agent="Commerce_Lead",
                dependencies=[t2.task_id],
                risk_level=RiskLevel.L1
            )
            tasks = [t1, t2, t3]

        # CATEGORY 4: PLANNING / PROPOSAL ONLY (Read-Only Engineering)
        elif parsed.category == ObjectiveCategory.PLANNING:
            t1 = TaskNode(
                title="Inspect Repository Architecture & Current State",
                objective="Inspect codebase structure and analyze existing technical patterns",
                project=project,
                assigned_agent="Engineering_Lead",
                risk_level=RiskLevel.L0
            )
            t2 = TaskNode(
                title="Review Security, Isolation & Architectural Debt",
                objective="Evaluate security boundaries and identify architectural bottlenecks",
                project=project,
                assigned_agent="Cybersecurity_Lead",
                dependencies=[t1.task_id],
                risk_level=RiskLevel.L0
            )
            t3 = TaskNode(
                title="Propose Three Highest-Value Engineering Improvements",
                objective="Formulate exactly three viable improvements with trade-offs without editing files",
                project=project,
                assigned_agent="Engineering_Lead",
                dependencies=[t2.task_id],
                risk_level=RiskLevel.L1,
                inputs={"read_only": True}
            )
            tasks = [t1, t2, t3]

        # CATEGORY 5: CYBERSECURITY REVIEW
        elif parsed.category == ObjectiveCategory.CYBERSECURITY_REVIEW:
            t1 = TaskNode(
                title="Audit Credential & Secret Isolation Boundaries",
                objective="Inspect vault references, environment variables, and token sanitization",
                project=project,
                assigned_agent="Cybersecurity_Lead",
                risk_level=RiskLevel.L0
            )
            t2 = TaskNode(
                title="Verify Path Sandboxing & Capability Grants",
                objective="Inspect filesystem permissions, tool gateway enforcement, and least privilege",
                project=project,
                assigned_agent="Cybersecurity_Lead",
                dependencies=[t1.task_id],
                risk_level=RiskLevel.L0
            )
            t3 = TaskNode(
                title="Assemble Threat Assessment & Defensive Hardening Plan",
                objective="Produce prioritized AppSec and security recommendations",
                project=project,
                assigned_agent="Cybersecurity_Lead",
                dependencies=[t2.task_id],
                risk_level=RiskLevel.L1
            )
            tasks = [t1, t2, t3]

        # CATEGORY 6: SOFTWARE DEVELOPMENT BREAK/FIX (Only when permitted and not read-only)
        elif parsed.category == ObjectiveCategory.SOFTWARE_DEVELOPMENT:
            t1 = TaskNode(
                title="Inspect Project Stack & Requirements",
                objective="Inspect codebase and analyze development requirements",
                project=project,
                assigned_agent="Engineering_Lead",
                risk_level=RiskLevel.L1
            )
            t2 = TaskNode(
                title="Security & Isolation Review",
                objective="Review security, credential isolation, and sandboxing posture",
                project=project,
                assigned_agent="Cybersecurity_Lead",
                dependencies=[t1.task_id],
                risk_level=RiskLevel.L1
            )
            t3 = TaskNode(
                title="Execute Core Implementation & Verification",
                objective="Build, verify implementation, and run unit tests",
                project=project,
                assigned_agent="Engineering_Lead",
                dependencies=[t2.task_id],
                risk_level=RiskLevel.L2
            )
            tasks = [t1, t2, t3]

        # DEFAULT: MULTI-DOMAIN
        else:
            t1 = TaskNode(
                title=f"Analyze Objective: {objective_text[:40]}...",
                objective=objective_text,
                project=project,
                assigned_agent="Operations_Lead",
                risk_level=RiskLevel.L1
            )
            t2 = TaskNode(
                title="Technical & Security Review",
                objective="Assess feasibility and policy compliance",
                project=project,
                assigned_agent="Cybersecurity_Lead",
                dependencies=[t1.task_id],
                risk_level=RiskLevel.L1
            )
            t3 = TaskNode(
                title="Synthesize Results & Recommendations",
                objective="Produce finalized recommendations",
                project=project,
                assigned_agent="Engineering_Lead",
                dependencies=[t2.task_id],
                risk_level=RiskLevel.L1
            )
            tasks = [t1, t2, t3]

        return parsed, tasks


class PlanResultSynthesizer:
    """Produces executive natural-language responses speaking to Zak with clear evidence and structure."""

    @classmethod
    def synthesize(
        cls,
        parsed: ParsedObjective,
        executed_tasks: Dict[str, TaskNode],
        context_bus: Optional[SharedContextBus] = None,
        diagnostics: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        agents_used = list({t.assigned_agent for t in executed_tasks.values()})
        tools_used = set()
        evidence_collected = []
        findings = []

        for t in executed_tasks.values():
            if t.response:
                tools_used.update(t.response.tools_used)
                evidence_collected.extend([e.claim for e in t.response.evidence])
                if isinstance(t.response.result, dict):
                    findings.append(t.response.result)
                elif isinstance(t.response.result, str):
                    findings.append({"task": t.title, "result": t.response.result})

        diag = diagnostics or SystemDiagnosticsCollector.collect()

        # Human-readable report. It states only what happened: each task's real
        # status and output. Lead-agent output is model analysis, never verified
        # work, and is labelled as such; no pre-written findings are added.
        completed = sum(1 for t in executed_tasks.values() if t.status.value == "COMPLETED")
        failed = sum(1 for t in executed_tasks.values() if t.status.value == "FAILED")
        lines = [
            f"Objective: {parsed.description}",
            f"Category: {parsed.category.value}",
            f"Tasks: {len(executed_tasks)} run, {completed} finished, {failed} failed. "
            "These are analyses only: nothing was built, changed or independently verified.",
            "",
        ]
        for t in executed_tasks.values():
            lines.append(f"- {t.title} ({t.assigned_agent}): {t.status.value}")
            result = t.response.result if t.response else None
            text = result.get("analysis") if isinstance(result, dict) else result
            if isinstance(result, dict) and result.get("simulated"):
                text = "(simulated output, no live model) " + str(text or "")
            if text:
                lines.append("  " + str(text).strip().replace("\n", " ")[:600])
        lines += [
            "",
            "Measured on this machine:",
            f"  - Host OS: {diag['hardware']['os']} | CPU: {diag['hardware']['processor']} ({diag['hardware']['cpu_cores']} cores)",
            f"  - Memory: {diag['hardware']['ram_total_gb']} GB RAM | Storage: {diag['hardware']['disk_free_gb']} GB free of {diag['hardware']['disk_total_gb']} GB",
            f"  - Providers: {', '.join([f'{k}: {v}' for k, v in diag['providers'].items()])}",
            f"  - Tools registered in the tool gateway: {diag['active_tools_count']}",
            "",
            "To have HOOD actually build or change something, start an agent mission "
            "(/mission followed by the objective): you approve the plan, agents do the work, "
            "and an independent verifier checks it.",
        ]

        formatted_output = "\n".join(lines)

        return {
            "formatted_text": formatted_output,
            "parsed_objective": parsed.model_dump(),
            "diagnostics": diag,
            "agents_used": agents_used,
            "tools_used": list(tools_used),
            "evidence_count": len(evidence_collected),
            "tasks_summary": {t.title: t.status.value for t in executed_tasks.values()}
        }
