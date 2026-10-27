"""Pydantic ticket contract carried over from the source notebook."""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

CATEGORIES = {
    "Shipping",
    "Returns & Refunds",
    "Account & Login",
    "Payment & Billing",
    "Technical Issue",
}


class TicketContract(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=False)

    ticket_id: str
    customer_message: str
    agent_response: str
    category: str
    channel: str
    created_at: str
    kb_reference: str | None = None

    @field_validator("customer_message")
    @classmethod
    def message_must_be_useful(cls, value: str) -> str:
        if len(value.strip()) < 5:
            raise ValueError(
                "customer_message is empty or too short to be a real ticket"
            )
        return value.strip()

    @field_validator("agent_response")
    @classmethod
    def response_is_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("agent_response is missing")
        return value.strip()

    @field_validator("category")
    @classmethod
    def category_must_be_known(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"category {value!r} is not a known support category")
        return value


def validate_batch(records: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """Split source records into contract-valid and rejected rows with reasons."""
    valid: list[dict] = []
    rejected: list[dict] = []
    for record in records:
        try:
            valid.append(TicketContract.model_validate(record).model_dump())
        except Exception as exc:
            rejected.append({**record, "_rejection_reason": str(exc)})
    return valid, rejected
