"""Optional Great Expectations checkpoint; the default gate stays lightweight."""

from typing import Any

from support_backend.contracts import CATEGORIES


def run_great_expectations(rows: list[dict[str, Any]]) -> dict:
    try:
        import great_expectations as gx
        import great_expectations.expectations as gxe
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "QUALITY_MODE=great-expectations requires the optional quality extra. "
            "Install with `pip install '.[quality]'`."
        ) from exc

    if not rows:
        return {"success": True, "statistics": {"evaluated_expectations": 0}}
    dataframe = pd.DataFrame(rows)
    context = gx.get_context(mode="ephemeral")
    source = context.data_sources.add_pandas("support_ticket_source")
    asset = source.add_dataframe_asset(name="support_tickets")
    batch_definition = asset.add_batch_definition_whole_dataframe("whole_silver_table")
    suite = context.suites.add(
        gx.ExpectationSuite(name="support_tickets_quality_suite")
    )
    suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="ticket_id"))
    suite.add_expectation(
        gxe.ExpectColumnValuesToNotBeNull(column="customer_message")
    )
    suite.add_expectation(gxe.ExpectColumnValuesToNotBeNull(column="agent_response"))
    suite.add_expectation(
        gxe.ExpectColumnValuesToBeInSet(
            column="category", value_set=sorted(CATEGORIES)
        )
    )
    suite.add_expectation(
        gxe.ExpectColumnValueLengthsToBeBetween(
            column="customer_message", min_value=5
        )
    )
    suite.add_expectation(gxe.ExpectColumnValuesToBeUnique(column="ticket_id"))
    result = batch_definition.get_batch(
        batch_parameters={"dataframe": dataframe}
    ).validate(suite)
    return {"success": bool(result.success), "statistics": result.statistics}
