"""Operational risk and scenario analytics with explicit, inspectable assumptions."""

from datetime import UTC, datetime
import os
from typing import Any

from support_backend.store import (
    all_support_tickets,
    apply_escalation,
    financial_assumptions,
    save_financial_assumptions,
)

SLA_TARGET_HOURS = {"urgent": 4.0, "high": 8.0, "normal": 24.0, "low": 48.0}


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _risk_for(ticket: dict[str, Any], now: datetime) -> dict:
    target = SLA_TARGET_HOURS.get(ticket.get("priority", "normal"), 24.0)
    created = _parse_datetime(ticket["created_at"])
    hours_open = max(0.0, (now - created).total_seconds() / 3600)
    age_ratio = min(hours_open / target, 1.0)
    priority_weight = {"urgent": 0.24, "high": 0.15, "normal": 0.05, "low": 0.0}.get(
        ticket.get("priority", "normal"), 0.05
    )
    sentiment_weight = 0.16 if ticket.get("sentiment") == "negative" else 0.04 if ticket.get("sentiment") == "neutral" else 0.0
    score = min(1.0, 0.6 * age_ratio + priority_weight + sentiment_weight)
    if hours_open >= target:
        state = "breached"
    elif hours_open >= target * 0.75 or score >= 0.62:
        state = "at_risk"
    else:
        state = "on_track"

    reasons = []
    if hours_open >= target:
        reasons.append(f"open for {hours_open:.1f}h against a {target:.0f}h first-response target")
    elif hours_open >= target * 0.75:
        reasons.append("approaching the first-response target")
    if ticket.get("priority") in ("urgent", "high"):
        reasons.append(f"{ticket['priority']} priority")
    if ticket.get("sentiment") == "negative":
        reasons.append("negative customer sentiment signal")
    return {
        "ticket_id": ticket["ticket_id"],
        "category": ticket["category"],
        "created_at": ticket["created_at"],
        "priority": ticket["priority"],
        "sentiment": ticket["sentiment"],
        "status": ticket["status"],
        "hours_open": round(hours_open, 1),
        "sla_target_hours": target,
        "risk_score": round(score, 3),
        "risk_state": state,
        "reason": "; ".join(reasons) or "within the current response target",
    }


def operations_analytics() -> dict:
    now = datetime.now(UTC)
    tickets = all_support_tickets()
    pending = [ticket for ticket in tickets if ticket["status"] in ("open", "escalated")]
    risk_rows = [_risk_for(ticket, now) for ticket in pending]
    counts = {state: 0 for state in ("on_track", "at_risk", "breached")}
    for row in risk_rows:
        counts[row["risk_state"]] += 1

    sentiments = []
    total = len(tickets)
    for sentiment in ("positive", "neutral", "negative"):
        count = sum(ticket["sentiment"] == sentiment for ticket in tickets)
        sentiments.append(
            {
                "sentiment": sentiment,
                "count": count,
                "share_percent": round(100 * count / total, 1) if total else 0.0,
            }
        )
    risk_rows.sort(
        key=lambda row: (
            row["risk_state"] == "breached",
            row["risk_score"],
            row["hours_open"],
        ),
        reverse=True,
    )
    return {
        "generated_at": now.isoformat(),
        "dataset_notice": (
            "This workspace currently uses synthetic demonstration tickets."
            if not os.getenv("SUPPORT_DATA_LABEL")
            else os.getenv("SUPPORT_DATA_LABEL")
        ),
        "total_tickets": total,
        "pending_tickets": len(pending),
        "on_track": counts["on_track"],
        "at_risk": counts["at_risk"],
        "breached": counts["breached"],
        "sentiment": sentiments,
        "high_risk_tickets": risk_rows[:12],
        "model_note": (
            "Risk scores are transparent rule-based estimates using ticket age, priority, "
            "and a keyword sentiment signal. They are not trained or validated predictions. "
            "Assumed first-response targets: urgent 4h, high 8h, normal 24h, low 48h."
        ),
    }


def _calculate_financials(assumptions: dict) -> dict:
    monthly = float(assumptions["monthly_tickets"])
    hourly_cost = float(assumptions["agent_hourly_cost_sar"])
    handle_minutes = float(assumptions["avg_handle_minutes"])
    deflection = float(assumptions["automation_deflection_pct"]) / 100
    ai_cost_per_ticket = float(assumptions["ai_cost_per_ticket_sar"])
    implementation = float(assumptions["implementation_cost_sar"])

    manual_unit = hourly_cost * handle_minutes / 60
    manual_total = monthly * manual_unit
    automated = monthly * deflection
    ai_total = automated * ai_cost_per_ticket
    remaining_manual = (monthly - automated) * manual_unit
    after_total = remaining_manual + ai_total
    monthly_savings = manual_total - after_total
    annual_net = monthly_savings * 12 - implementation
    year_one_investment = (ai_total * 12) + implementation
    roi = (
        ((annual_net / year_one_investment) * 100)
        if year_one_investment > 0
        else 0.0
    )
    payback = (
        implementation / monthly_savings
        if monthly_savings > 0 and implementation > 0
        else None
    )
    return {
        "manual_cost_per_ticket_sar": round(manual_unit, 2),
        "manual_monthly_cost_sar": round(manual_total, 2),
        "automated_tickets": round(automated, 1),
        "ai_monthly_cost_sar": round(ai_total, 2),
        "total_monthly_cost_after_sar": round(after_total, 2),
        "monthly_net_savings_sar": round(monthly_savings, 2),
        "annual_net_savings_sar": round(annual_net, 2),
        "roi_percent": round(roi, 1),
        "payback_months": round(payback, 1) if payback is not None else None,
    }


def financial_scenario() -> dict:
    assumptions = financial_assumptions()
    return {
        "assumptions": assumptions,
        "metrics": _calculate_financials(assumptions),
        "notice": (
            "Illustrative scenario in SAR. Every input is an editable assumption, not a "
            "measured company cost or a forecast; validate with actual payroll, handling-time, "
            "AI usage, implementation, and resolution data."
        ),
    }


def update_financial_scenario(assumptions: dict) -> dict:
    saved = save_financial_assumptions(assumptions)
    return {
        "assumptions": saved,
        "metrics": _calculate_financials(saved),
        "notice": (
            "Illustrative scenario in SAR. Every input is an editable assumption, not a "
            "measured company cost or a forecast; validate with actual payroll, handling-time, "
            "AI usage, implementation, and resolution data."
        ),
    }


def run_escalation_automation(*, dry_run: bool = True, limit: int = 1000) -> dict:
    now = datetime.now(UTC)
    open_tickets = [
        ticket
        for ticket in all_support_tickets()
        if ticket["status"] == "open"
    ][: max(1, min(limit, 1000))]
    decisions = []
    for ticket in open_tickets:
        risk = _risk_for(ticket, now)
        reasons = []
        if ticket["priority"] == "urgent":
            reasons.append("urgent priority")
        if risk["risk_state"] == "breached":
            reasons.append("first-response target breached")
        elif risk["risk_state"] == "at_risk" and ticket["sentiment"] == "negative":
            reasons.append("negative sentiment while approaching response target")
        if not reasons:
            continue
        reason = "; ".join(reasons)
        applied = (
            apply_escalation(ticket["ticket_id"], reason, risk["risk_score"])
            if not dry_run
            else False
        )
        decisions.append(
            {
                "ticket_id": ticket["ticket_id"],
                "reason": reason,
                "risk_score": risk["risk_score"],
                "applied": applied,
            }
        )
    return {
        "dry_run": dry_run,
        "examined": len(open_tickets),
        "candidates": len(decisions),
        "escalated": sum(item["applied"] for item in decisions),
        "events": decisions[:100],
        "policy_note": (
            "Rule-based assist only. A preview is non-mutating; applying rules updates open "
            "tickets to escalated and records an audit event. Customer messages are never sent."
        ),
    }
