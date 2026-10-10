"""
HOOD Economic Engine & Revenue Generation Service
Governed by Master System Specification v1.2 Sections 11, 12, 13, 14.

Features:
1. Multi-Channel Opportunity Discovery:
   - Freelance development, client solutions, automation services, SaaS, digital products, consulting.
2. Independent Opportunity Killer:
   - Rigorously stresses assumptions, unit economics, risks, delivery efforts, and rejects weak opportunities.
3. Autonomous Business Operations State Machine:
   - DISCOVERY -> RESEARCH -> QUALIFIED -> PROPOSAL_DRAFT -> APPROVAL_REQUIRED -> DELIVERED -> PRODUCTIZED.
   - External messages, bids, payments strictly gated by Zak's approval.
4. Wealth & Financial Intelligence Modes:
   - NORMAL, GROWTH, FINANCIAL_SURVIVAL.
5. Rent-versus-Own Calculator:
   - Evaluates API cost vs local GPU inference amortization and internal deterministic tool conversion.
"""

from __future__ import annotations
import json
import sqlite3
from enum import Enum
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from packages.config.paths import store_path


class FinancialMode(str, Enum):
    NORMAL = "NORMAL"                     # Balance short-term income and long-term asset growth
    GROWTH = "GROWTH"                     # Prioritize scalable assets and strategic capabilities
    FINANCIAL_SURVIVAL = "FINANCIAL_SURVIVAL" # Eliminate avoidable costs, prioritize immediate free/cash generation


class OpportunityStatus(str, Enum):
    IDENTIFIED = "IDENTIFIED"
    KILLED_REJECTED = "KILLED_REJECTED"
    QUALIFIED = "QUALIFIED"
    PROPOSAL_PREPARED = "PROPOSAL_PREPARED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    DELIVERED = "DELIVERED"
    PRODUCTIZED = "PRODUCTIZED"


class OpportunityEvaluation(BaseModel):
    potential_customer_segment: str
    problem_solved: str
    expected_revenue_usd: float
    expected_profit_usd: float
    capital_required_usd: float
    time_to_first_revenue_days: int
    delivery_effort_hours: float
    probability_of_success: float          # 0.0 to 1.0
    competitive_intensity: str             # LOW, MEDIUM, HIGH, SEVERE
    compliance_legal_risks: List[str] = Field(default_factory=list)
    reputation_risks: List[str] = Field(default_factory=list)
    scalability_score: float               # 1 to 10
    recurring_revenue_potential: bool = False
    reusable_asset_created: bool = False
    strategic_value_score: float           # 1 to 10
    roi_hourly_rate_usd: float = 0.0


class OpportunityKillerReport(BaseModel):
    is_killed: bool
    rejection_reasons: List[str] = Field(default_factory=list)
    fatal_flaws: List[str] = Field(default_factory=list)
    survival_score: float                  # 0.0 (dead) to 1.0 (resilient)
    critique: str


class RentVersusOwnAnalysis(BaseModel):
    service_name: str
    monthly_api_rent_cost_usd: float
    annual_api_rent_cost_usd: float
    replacement_engineering_hours: float
    local_hardware_cost_usd: float
    break_even_months: float
    strategic_independence_gain: str
    recommended_path: str  # RENT, BUILD_OWN, HYBRID
    rationale: str


class OpportunityRecord(BaseModel):
    opportunity_id: str
    title: str
    category: str
    evaluation: OpportunityEvaluation
    killer_report: OpportunityKillerReport
    status: OpportunityStatus
    created_at: str
    updated_at: str
    approval_id: Optional[str] = None
    delivery_notes: Optional[str] = None


class EconomicEngine:
    """Manages revenue discovery, opportunity vetting, business workflows, and rent-vs-own decisions."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = store_path(db_path, "artifacts/economic_engine.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.current_financial_mode = FinancialMode.NORMAL
        self._init_sqlite()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sqlite(self):
        with self._get_connection() as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                opportunity_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                evaluation TEXT NOT NULL,
                killer_report TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                approval_id TEXT,
                delivery_notes TEXT
            );
            """)

    def set_financial_mode(self, mode: FinancialMode) -> None:
        self.current_financial_mode = mode

    def evaluate_and_stress_test_opportunity(
        self,
        opportunity_id: str,
        title: str,
        category: str,
        evaluation: OpportunityEvaluation
    ) -> OpportunityRecord:
        """
        Runs an opportunity through the independent Opportunity Killer.
        Rejects opportunities with unrealistic margins, excessive capital requirements,
        severe compliance risks, or sub-target hourly return.
        """
        # Calculate hourly return
        if evaluation.delivery_effort_hours > 0:
            evaluation.roi_hourly_rate_usd = round(
                (evaluation.expected_profit_usd - evaluation.capital_required_usd) / evaluation.delivery_effort_hours, 2
            )

        killer_report = self._run_opportunity_killer(evaluation)
        initial_status = OpportunityStatus.KILLED_REJECTED if killer_report.is_killed else OpportunityStatus.QUALIFIED

        now = datetime.now(timezone.utc).isoformat()
        opp = OpportunityRecord(
            opportunity_id=opportunity_id,
            title=title,
            category=category,
            evaluation=evaluation,
            killer_report=killer_report,
            status=initial_status,
            created_at=now,
            updated_at=now
        )

        with self._get_connection() as conn:
            conn.execute("""
            INSERT OR REPLACE INTO opportunities (
                opportunity_id, title, category, evaluation, killer_report,
                status, created_at, updated_at, approval_id, delivery_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                opp.opportunity_id,
                opp.title,
                opp.category,
                opp.evaluation.model_dump_json(),
                opp.killer_report.model_dump_json(),
                opp.status.value,
                opp.created_at,
                opp.updated_at,
                opp.approval_id,
                opp.delivery_notes
            ))
        return opp

    def _run_opportunity_killer(self, eval_data: OpportunityEvaluation) -> OpportunityKillerReport:
        """Adversarial stress-testing of economic assumptions."""
        rejections = []
        fatal_flaws = []

        # 1. Financial check: Zero spend constitution / Capital requirement
        if eval_data.capital_required_usd > 0:
            rejections.append(f"Requires upfront capital (${eval_data.capital_required_usd}), violating $0 budget unless authorized.")

        # 2. Hourly productivity bar
        if eval_data.roi_hourly_rate_usd < 35.0:
            rejections.append(f"Effective return (${eval_data.roi_hourly_rate_usd}/hr) falls below minimum viable threshold ($35/hr).")

        # 3. Probability of success
        if eval_data.probability_of_success < 0.35:
            rejections.append(f"Success probability ({eval_data.probability_of_success * 100:.0f}%) is too low for unhedged commitment.")

        # 4. Severe legal or reputation risk
        if eval_data.compliance_legal_risks:
            fatal_flaws.extend(eval_data.compliance_legal_risks)
            rejections.append("Unacceptable compliance or legal liability detected.")

        if eval_data.reputation_risks:
            fatal_flaws.extend(eval_data.reputation_risks)
            rejections.append("Unacceptable reputation risk detected.")

        is_killed = len(rejections) > 0 or len(fatal_flaws) > 0
        survival_score = max(0.0, 1.0 - (len(rejections) * 0.25) - (len(fatal_flaws) * 0.5))

        critique = "Passed stress testing." if not is_killed else f"Rejected with {len(rejections)} issues: {'; '.join(rejections)}"

        return OpportunityKillerReport(
            is_killed=is_killed,
            rejection_reasons=rejections,
            fatal_flaws=fatal_flaws,
            survival_score=round(survival_score, 2),
            critique=critique
        )

    def calculate_rent_versus_own(
        self,
        service_name: str,
        monthly_api_rent_cost_usd: float,
        replacement_engineering_hours: float,
        local_hardware_cost_usd: float = 0.0,
        hourly_dev_value_usd: float = 50.0
    ) -> RentVersusOwnAnalysis:
        """Calculates precise break-even and strategic trade-off for replacing external SaaS/APIs."""
        annual_rent = monthly_api_rent_cost_usd * 12.0
        build_cost = (replacement_engineering_hours * hourly_dev_value_usd) + local_hardware_cost_usd

        if monthly_api_rent_cost_usd <= 0:
            break_even_months = float("inf")
            rec = "RENT"
            rationale = "Service is free or negligible; building an owned alternative yields negative financial ROI."
        else:
            break_even_months = round(build_cost / monthly_api_rent_cost_usd, 1)
            if break_even_months <= 6.0:
                rec = "BUILD_OWN"
                rationale = f"Rapid break-even ({break_even_months} months); building owned capability saves ${annual_rent:.2f}/yr."
            elif break_even_months <= 18.0:
                rec = "HYBRID"
                rationale = f"Moderate break-even ({break_even_months} months); build in background using shadow execution."
            else:
                rec = "RENT"
                rationale = f"Extended break-even ({break_even_months} months); renting remains economically superior."

        return RentVersusOwnAnalysis(
            service_name=service_name,
            monthly_api_rent_cost_usd=monthly_api_rent_cost_usd,
            annual_api_rent_cost_usd=annual_rent,
            replacement_engineering_hours=replacement_engineering_hours,
            local_hardware_cost_usd=local_hardware_cost_usd,
            break_even_months=break_even_months,
            strategic_independence_gain="HIGH" if rec in ("BUILD_OWN", "HYBRID") else "LOW",
            recommended_path=rec,
            rationale=rationale
        )

    def list_qualified_opportunities(self) -> List[OpportunityRecord]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM opportunities WHERE status = 'QUALIFIED'").fetchall()
            return [self._row_to_record(r) for r in rows]

    def _row_to_record(self, row: sqlite3.Row) -> OpportunityRecord:
        return OpportunityRecord(
            opportunity_id=row["opportunity_id"],
            title=row["title"],
            category=row["category"],
            evaluation=OpportunityEvaluation.model_validate_json(row["evaluation"]),
            killer_report=OpportunityKillerReport.model_validate_json(row["killer_report"]),
            status=OpportunityStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            approval_id=row["approval_id"],
            delivery_notes=row["delivery_notes"]
        )
