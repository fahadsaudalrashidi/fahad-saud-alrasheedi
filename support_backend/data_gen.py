"""Synthetic demo data, retaining the categories and policy examples in the notebook."""

import random
import uuid
from datetime import UTC, datetime, timedelta

CATEGORIES = [
    "Shipping",
    "Returns & Refunds",
    "Account & Login",
    "Payment & Billing",
    "Technical Issue",
]

KB_ARTICLES = [
    {"id": "kb-001", "category": "Shipping", "title": "Tracking a delayed shipment", "content": "If tracking has not updated in more than 5 business days, the shipment may be stuck at a customs or carrier hub. Ask for the order number and check the carrier portal. Delays over 10 business days qualify for a reshipment or refund."},
    {"id": "kb-002", "category": "Shipping", "title": "Wrong address entered at checkout", "content": "Once an order has shipped, the address cannot be changed. Ask the customer to contact the carrier directly to redirect the package. If the carrier cannot redirect it, treat it as a lost package."},
    {"id": "kb-003", "category": "Shipping", "title": "International customs fees", "content": "Customers outside the seller's home country may be charged customs duties on delivery. These fees are set by the destination country and are not collected by us, so they cannot be refunded."},
    {"id": "kb-004", "category": "Returns & Refunds", "title": "Standard return window", "content": "Customers can return unused items within 30 days of delivery for a full refund to the original payment method. Items must be in original packaging with tags attached."},
    {"id": "kb-005", "category": "Returns & Refunds", "title": "Refund processing time", "content": "Once a returned item is received at the warehouse, refunds are issued within 3–5 business days. It can take an additional 2–7 business days for the bank to post the credit."},
    {"id": "kb-006", "category": "Returns & Refunds", "title": "Damaged item on arrival", "content": "If an item arrives damaged, ask for photos of the damage and shipping box. Damaged items are eligible for an immediate replacement or full refund without needing to return the item first."},
    {"id": "kb-007", "category": "Account & Login", "title": "Password reset not arriving", "content": "Password reset emails can take up to 10 minutes and are often filtered to spam. If it still has not arrived, verify that the email on file matches exactly, including typos or old email addresses."},
    {"id": "kb-008", "category": "Account & Login", "title": "Account locked after failed logins", "content": "Accounts lock automatically after 5 failed login attempts within 15 minutes. The lock clears after 30 minutes, or an agent can clear it manually after verifying identity."},
    {"id": "kb-009", "category": "Account & Login", "title": "Merging two accounts", "content": "We cannot automatically merge two customer accounts. Order history from the secondary account can be copied by an agent upon request, but loyalty points cannot be transferred."},
    {"id": "kb-010", "category": "Payment & Billing", "title": "Duplicate charge on card", "content": "Duplicate charges are usually a temporary authorization hold and disappear within 5–7 business days. If both charges post permanently, escalate to billing for a manual refund."},
    {"id": "kb-011", "category": "Payment & Billing", "title": "Coupon code not applying", "content": "Coupon codes are case-sensitive and may have a minimum order value or be limited to specific product categories. Check the coupon's terms before assuming a system bug."},
    {"id": "kb-012", "category": "Payment & Billing", "title": "Invoice needed for business purchase", "content": "A formatted invoice with tax details can be generated from the order page under Download Invoice. If the business needs a different billing name, an agent can reissue it manually."},
    {"id": "kb-013", "category": "Technical Issue", "title": "App crashing on checkout", "content": "Checkout crashes are most often caused by an outdated app version or a corrupted cart cache. Ask the customer to update the app and clear the cart before retrying."},
    {"id": "kb-014", "category": "Technical Issue", "title": "Product images not loading", "content": "Missing product images are usually a CDN caching issue on the customer's network. Suggest switching networks or waiting 10–15 minutes; this is not an account-specific problem."},
]

CUSTOMER_TEMPLATES = {
    "Shipping": [
        "My order {order} hasn't moved in tracking for over a week, where is it?",
        "I put the wrong address on order {order}, can you fix it, it already shipped",
        "Why was I charged customs fees on my order from another country?",
    ],
    "Returns & Refunds": [
        "I want to return order {order}, it's been 20 days since delivery",
        "When will I get my refund for order {order}? It's been 10 days since you received it",
        "My order {order} arrived completely smashed, box was crushed",
    ],
    "Account & Login": [
        "The password reset email never arrived, I've waited 30 minutes",
        "My account got locked after I mistyped my password a few times",
        "Can you merge my two accounts, I have points on both",
    ],
    "Payment & Billing": [
        "I see two charges on my card for order {order}, was I double charged?",
        "My coupon code SAVE20 isn't applying at checkout",
        "I need a business invoice for order {order} with my company name on it",
    ],
    "Technical Issue": [
        "The app keeps crashing every time I try to check out",
        "Product images aren't loading at all on my phone",
    ],
}

KB_BY_TEMPLATE = {
    ("Shipping", 0): "kb-001", ("Shipping", 1): "kb-002", ("Shipping", 2): "kb-003",
    ("Returns & Refunds", 0): "kb-004", ("Returns & Refunds", 1): "kb-005", ("Returns & Refunds", 2): "kb-006",
    ("Account & Login", 0): "kb-007", ("Account & Login", 1): "kb-008", ("Account & Login", 2): "kb-009",
    ("Payment & Billing", 0): "kb-010", ("Payment & Billing", 1): "kb-011", ("Payment & Billing", 2): "kb-012",
    ("Technical Issue", 0): "kb-013", ("Technical Issue", 1): "kb-014",
}

AGENT_RESPONSES = {
    "kb-001": "Tracking can pause at a carrier hub. If there is no update within 10 business days total, the order qualifies for a reshipment or refund.",
    "kb-002": "After shipment, contact the carrier to request a redirect. If they cannot redirect it, we will treat the package as lost.",
    "kb-003": "Customs fees are set by the destination country and are not collected by us, so we cannot refund them.",
    "kb-004": "Unused items can be returned within 30 days of delivery in their original packaging with tags attached.",
    "kb-005": "Refunds are issued 3–5 business days after the warehouse receives the return. Banks may take another 2–7 business days to post the credit.",
    "kb-006": "Please send photos of the damaged item and shipping box. A replacement or full refund is available without returning the item.",
    "kb-007": "Reset emails can take up to 10 minutes and may land in spam. Please confirm the exact email address on your account.",
    "kb-008": "Accounts unlock after 30 minutes. An agent can also clear the lock after verifying your identity.",
    "kb-009": "We cannot automatically merge accounts, but an agent can copy order history. Loyalty points cannot be transferred.",
    "kb-010": "A second card charge is often a temporary hold that clears within 5–7 business days. If both charges post, billing can issue a manual refund.",
    "kb-011": "Coupon codes are case-sensitive and may have minimum order or category restrictions. Check the terms before retrying.",
    "kb-012": "Download a tax-formatted invoice from the order page. An agent can reissue it if the billing name needs to change.",
    "kb-013": "Please update the app and clear the cart cache, then try checkout again.",
    "kb-014": "Try switching networks or waiting 10–15 minutes; missing images are usually caused by network or CDN caching.",
}


def generate_kb_articles() -> list[dict]:
    return [dict(article) for article in KB_ARTICLES]


def infer_priority(message: str) -> tuple[str, str]:
    lowered = message.lower()
    negative = ("angry", "broken", "smashed", "charged twice", "locked", "crashing", "lost")
    urgent = ("fraud", "urgent", "immediately", "double charged", "charged twice", "security")
    sentiment = "negative" if any(word in lowered for word in negative) else "neutral"
    if any(word in lowered for word in urgent):
        return "urgent", sentiment
    if sentiment == "negative":
        return "high", sentiment
    return "normal", sentiment


def generate_tickets(n: int = 600, bad_ratio: float = 0.12) -> list[dict]:
    """Generate source-compatible synthetic tickets with deliberate contract failures."""
    rng = random.Random()
    now = datetime.now(UTC)
    tickets: list[dict] = []
    for _ in range(n):
        category = rng.choice(CATEGORIES)
        templates = CUSTOMER_TEMPLATES[category]
        template_index = rng.randrange(len(templates))
        order_number = f"ORD-{rng.randint(100000, 999999)}"
        message = templates[template_index].format(order=order_number)
        article_id = KB_BY_TEMPLATE[(category, template_index)]
        priority, sentiment = infer_priority(message)
        created_at = now - timedelta(minutes=rng.randint(0, 60 * 24 * 30))
        tickets.append({
            "ticket_id": f"T-{uuid.uuid4().hex[:10]}",
            "customer_message": message,
            "agent_response": AGENT_RESPONSES[article_id],
            "category": category,
            "channel": rng.choice(["chat", "email", "phone"]),
            "created_at": created_at.isoformat(),
            "kb_reference": article_id,
            "status": rng.choices(["open", "escalated", "resolved"], weights=[48, 14, 38])[0],
            "priority": priority,
            "sentiment": sentiment,
            "quality_status": "passed",
            "response_minutes": rng.randint(3, 65),
        })

    for position, index in enumerate(rng.sample(range(n), int(n * bad_ratio))):
        issue = position % 4
        if issue == 0:
            tickets[index]["customer_message"] = ""
        elif issue == 1:
            tickets[index]["customer_message"] = "help"
        elif issue == 2:
            tickets[index]["agent_response"] = None
        else:
            tickets[index]["category"] = "Unknown"

    for _ in range(max(1, n // 100)):
        tickets.append(dict(rng.choice(tickets)))
    return tickets
