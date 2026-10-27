import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from support_backend.market_strategy import get_market_strategy
from support_backend.main import TicketInput
from support_backend.operations import (
    financial_scenario,
    operations_analytics,
    run_escalation_automation,
    update_financial_scenario,
)
from support_backend.pipeline import run_pipeline
from support_backend.rag import RagIndex, chunk_articles
from support_backend.store import all_support_tickets, initialize_store


def ticket(ticket_id: str, **overrides) -> dict:
    record = {
        "ticket_id": ticket_id,
        "customer_message": "I need help with my account access immediately",
        "agent_response": "We are checking the account access issue.",
        "category": "Account & Login",
        "channel": "web",
        "created_at": datetime.now(UTC).isoformat(),
        "kb_reference": "kb-007",
        "status": "open",
        "priority": "normal",
        "quality_status": "passed",
        "sentiment": "neutral",
        "response_minutes": 6,
    }
    return {**record, **overrides}


class SupportOperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_env = {
            key: os.environ.get(key)
            for key in ("SUPPORT_DB_PATH", "LINEAGE_LOG_PATH", "RAG_MODE")
        }
        os.environ["SUPPORT_DB_PATH"] = str(Path(self.temp_dir.name) / "test.db")
        os.environ["LINEAGE_LOG_PATH"] = str(
            Path(self.temp_dir.name) / "lineage.jsonl"
        )
        os.environ["RAG_MODE"] = "hash"
        initialize_store()

    def tearDown(self):
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def test_pipeline_preserves_bronze_rejections_and_upserts_valid_silver(self):
        invalid = ticket(
            "ticket-invalid",
            customer_message="help",
            priority="normal",
        )
        result = run_pipeline(
            [
                ticket("ticket-valid"),
                ticket("ticket-urgent", priority="urgent", sentiment="negative"),
                invalid,
            ],
            rebuild_rag=lambda: 14,
        )

        self.assertEqual(result["received"], 3)
        self.assertEqual(result["accepted"], 2)
        self.assertEqual(result["rejected"], 1)
        self.assertTrue(result["quality_passed"])
        self.assertEqual(result["bronze_count"], 3)
        self.assertEqual(result["silver_count"], 2)
        self.assertEqual(result["gold_count"], 1)

    def test_escalation_preview_is_non_mutating_and_apply_is_audited(self):
        run_pipeline(
            [ticket("ticket-urgent", priority="urgent", sentiment="negative")],
            rebuild_rag=lambda: 14,
        )

        preview = run_escalation_automation(dry_run=True)
        self.assertGreaterEqual(preview["candidates"], 1)
        self.assertEqual(preview["escalated"], 0)
        self.assertEqual(all_support_tickets()[0]["status"], "open")

        applied = run_escalation_automation(dry_run=False)
        self.assertEqual(applied["escalated"], 1)
        self.assertEqual(all_support_tickets()[0]["status"], "escalated")
        self.assertEqual(run_escalation_automation(dry_run=False)["escalated"], 0)

    def test_financial_scenario_uses_and_persists_explicit_assumptions(self):
        initial = financial_scenario()
        self.assertEqual(initial["metrics"]["manual_cost_per_ticket_sar"], 15.0)
        self.assertEqual(initial["metrics"]["manual_monthly_cost_sar"], 9000.0)
        self.assertEqual(initial["metrics"]["roi_percent"], 1900.0)
        self.assertIsNone(initial["metrics"]["payback_months"])

        updated = update_financial_scenario(
            {
                "monthly_tickets": 100,
                "agent_hourly_cost_sar": 60,
                "avg_handle_minutes": 10,
                "automation_deflection_pct": 20,
                "ai_cost_per_ticket_sar": 1,
                "implementation_cost_sar": 500,
            }
        )
        self.assertEqual(updated["assumptions"]["monthly_tickets"], 100)
        self.assertEqual(updated["metrics"]["manual_cost_per_ticket_sar"], 10.0)
        self.assertEqual(updated["metrics"]["automated_tickets"], 20.0)
        self.assertEqual(updated["metrics"]["roi_percent"], 224.3)
        self.assertIsNotNone(updated["metrics"]["payback_months"])

    def test_arabic_content_is_searchable_and_market_sources_are_resolved(self):
        index = RagIndex(
            [
                {
                    "id": "kb-ar",
                    "category": "Shipping",
                    "title": "تحديث حالة الشحنة",
                    "content": "إذا لم تتغير حالة الشحنة خلال خمسة أيام عمل، تواصل مع شركة النقل للتحقق من التحديث.",
                    "language": "ar",
                }
            ]
        )
        matches = index.search("حالة الشحنة", top_k=1)
        self.assertEqual(matches[0]["article_id"], "kb-ar")
        self.assertEqual(matches[0]["language"], "ar")
        chunks = chunk_articles(
            [
                {
                    "id": "kb-ar",
                    "title": "تحديث حالة الشحنة",
                    "category": "Shipping",
                    "language": "ar",
                    "content": "الخطوة الأولى. الخطوة الثانية؟ الخطوة الثالثة۔",
                }
            ],
            max_sentences=1,
        )
        self.assertEqual(len(chunks), 3)

        english = get_market_strategy("en")
        arabic = get_market_strategy("ar")
        self.assertNotEqual(english["executive_summary"], arabic["executive_summary"])
        source_keys = {source["key"] for source in english["sources"]}
        self.assertGreaterEqual(len(source_keys), 20)
        for record in english["market_signals"] + english["competitors"]:
            self.assertTrue(set(record["source_keys"]).issubset(source_keys))

        analytics = operations_analytics()
        self.assertIn("not trained or validated predictions", analytics["model_note"])

    def test_ticket_api_contract_rejects_messages_shorter_than_five_characters(self):
        with self.assertRaises(ValueError):
            TicketInput(customer_message="help", category="Shipping")


if __name__ == "__main__":
    unittest.main()
