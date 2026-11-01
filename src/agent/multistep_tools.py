"""Original tool schemas for the hand-written multi-step set.

These schemas are written for this repository. They are not xLAM rows or xLAM
schemas, and they avoid every frozen held-out candidate tool in
``docs/CANDIDATES.md``.

The multi-step loop executes **scripted mock results** supplied by each task.
There is no live backend and no invented output: a mock result is part of the
task fixture, and the loop only returns what the fixture says.
"""

from __future__ import annotations

from typing import Any


def _object(
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _string(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _integer(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "integer", "description": description, **extra}


def _number(description: str, **extra: Any) -> dict[str, Any]:
    return {"type": "number", "description": description, **extra}


def _array(description: str, items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": "array", "description": description, "items": items, **extra}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_flights",
        "description": "Search available flights for one route and date.",
        "input_schema": _object(
            {
                "origin": _string("Origin city or airport code."),
                "destination": _string("Destination city or airport code."),
                "date": _string("Departure date, YYYY-MM-DD."),
            },
            ["origin", "destination", "date"],
        ),
    },
    {
        "name": "get_flight_details",
        "description": "Get details for one flight, including bag fee and seats left.",
        "input_schema": _object(
            {"flight_id": _string("Flight identifier from search_flights.")},
            ["flight_id"],
        ),
    },
    {
        "name": "book_flight",
        "description": "Book one flight for one passenger.",
        "input_schema": _object(
            {
                "flight_id": _string("Flight identifier from search_flights."),
                "passenger_name": _string("Passenger full name."),
                "seat": _string("Requested seat, for example 14C."),
            },
            ["flight_id", "passenger_name", "seat"],
        ),
    },
    {
        "name": "search_hotels",
        "description": "Search hotels in a city for a date range.",
        "input_schema": _object(
            {
                "city": _string("City name."),
                "check_in": _string("Check-in date, YYYY-MM-DD."),
                "check_out": _string("Check-out date, YYYY-MM-DD."),
            },
            ["city", "check_in", "check_out"],
        ),
    },
    {
        "name": "get_hotel_rooms",
        "description": "List rooms and nightly rates for one hotel.",
        "input_schema": _object(
            {
                "hotel_id": _string("Hotel identifier from search_hotels."),
                "check_in": _string("Check-in date, YYYY-MM-DD."),
                "check_out": _string("Check-out date, YYYY-MM-DD."),
            },
            ["hotel_id", "check_in", "check_out"],
        ),
    },
    {
        "name": "book_hotel",
        "description": "Book one hotel room for a guest.",
        "input_schema": _object(
            {
                "room_id": _string("Room identifier from get_hotel_rooms."),
                "guest_name": _string("Guest full name."),
                "nights": _integer("Number of nights.", minimum=1),
            },
            ["room_id", "guest_name", "nights"],
        ),
    },
    {
        "name": "find_customer",
        "description": "Find one customer by email address.",
        "input_schema": _object(
            {"email": _string("Customer email address.")},
            ["email"],
        ),
    },
    {
        "name": "list_orders",
        "description": "List orders for one customer, optionally filtered by status.",
        "input_schema": _object(
            {
                "customer_id": _string("Customer identifier from find_customer."),
                "status": {
                    "type": "string",
                    "enum": ["open", "delivered", "cancelled"],
                    "description": "Optional order status filter.",
                },
            },
            ["customer_id"],
        ),
    },
    {
        "name": "get_order_details",
        "description": "Get the line items and shipping charge for one order.",
        "input_schema": _object(
            {"order_id": _string("Order identifier from list_orders.")},
            ["order_id"],
        ),
    },
    {
        "name": "issue_refund",
        "description": "Issue a refund against one order.",
        "input_schema": _object(
            {
                "order_id": _string("Order identifier from list_orders."),
                "amount_usd": _number("Refund amount in USD.", minimum=0),
                "reason": _string("Short reason for the refund."),
            },
            ["order_id", "amount_usd", "reason"],
        ),
    },
    {
        "name": "find_invoice",
        "description": "Find one customer invoice for a month.",
        "input_schema": _object(
            {
                "customer_id": _string("Customer identifier from find_customer."),
                "month": _string("Billing month, YYYY-MM."),
            },
            ["customer_id", "month"],
        ),
    },
    {
        "name": "record_payment",
        "description": "Record a payment against one invoice.",
        "input_schema": _object(
            {
                "invoice_id": _string("Invoice identifier from find_invoice."),
                "amount_usd": _number("Payment amount in USD.", minimum=0),
                "method": {
                    "type": "string",
                    "enum": ["card", "bank_transfer", "credit"],
                    "description": "Payment method.",
                },
            },
            ["invoice_id", "amount_usd", "method"],
        ),
    },
    {
        "name": "get_usage",
        "description": "Get metered usage for one account and month.",
        "input_schema": _object(
            {
                "account_id": _string("Account identifier."),
                "month": _string("Usage month, YYYY-MM."),
            },
            ["account_id", "month"],
        ),
    },
    {
        "name": "apply_credit",
        "description": "Apply an account credit.",
        "input_schema": _object(
            {
                "account_id": _string("Account identifier."),
                "amount_usd": _number("Credit amount in USD.", minimum=0),
                "reason": _string("Short reason for the credit."),
            },
            ["account_id", "amount_usd", "reason"],
        ),
    },
    {
        "name": "lookup_product",
        "description": "Look up one product by SKU.",
        "input_schema": _object(
            {"sku": _string("Stock keeping unit.")},
            ["sku"],
        ),
    },
    {
        "name": "get_warehouse_stock",
        "description": "Get the stock level for one SKU at one warehouse.",
        "input_schema": _object(
            {
                "sku": _string("Stock keeping unit."),
                "warehouse": _string("Warehouse code."),
            },
            ["sku", "warehouse"],
        ),
    },
    {
        "name": "create_transfer",
        "description": "Create an inventory transfer between warehouses.",
        "input_schema": _object(
            {
                "sku": _string("Stock keeping unit."),
                "from_warehouse": _string("Source warehouse code."),
                "to_warehouse": _string("Destination warehouse code."),
                "quantity": _integer("Units to transfer.", minimum=1),
            },
            ["sku", "from_warehouse", "to_warehouse", "quantity"],
        ),
    },
    {
        "name": "search_contacts",
        "description": "Search the contact directory by name.",
        "input_schema": _object(
            {"name": _string("Full or partial contact name.")},
            ["name"],
        ),
    },
    {
        "name": "check_calendar",
        "description": "Read one day's busy blocks and free slots.",
        "input_schema": _object(
            {"date": _string("Date, YYYY-MM-DD.")},
            ["date"],
        ),
    },
    {
        "name": "schedule_meeting",
        "description": "Create one calendar meeting.",
        "input_schema": _object(
            {
                "title": _string("Meeting title."),
                "date": _string("Date, YYYY-MM-DD."),
                "start_time": _string("Start time, HH:MM."),
                "duration_minutes": _integer("Duration in minutes.", minimum=1),
                "attendee_ids": _array(
                    "Contact identifiers from search_contacts.",
                    _string("Contact identifier."),
                    minItems=1,
                ),
            },
            ["title", "date", "start_time", "duration_minutes", "attendee_ids"],
        ),
    },
    {
        "name": "search_documents",
        "description": "Search internal documents by text.",
        "input_schema": _object(
            {"query": _string("Search query.")},
            ["query"],
        ),
    },
    {
        "name": "read_document",
        "description": "Read one internal document.",
        "input_schema": _object(
            {"doc_id": _string("Document identifier from search_documents.")},
            ["doc_id"],
        ),
    },
    {
        "name": "post_message",
        "description": "Post a plain-text message to a channel.",
        "input_schema": _object(
            {
                "channel": _string("Channel name, for example #ops."),
                "text": _string("Message text."),
            },
            ["channel", "text"],
        ),
    },
    {
        "name": "create_task",
        "description": "Create one task assigned to a contact.",
        "input_schema": _object(
            {
                "title": _string("Task title."),
                "assignee_id": _string("Contact identifier from search_contacts."),
                "due_date": _string("Due date, YYYY-MM-DD."),
            },
            ["title", "assignee_id", "due_date"],
        ),
    },
    {
        "name": "get_weather_forecast",
        "description": "Get a daily weather forecast for one city.",
        "input_schema": _object(
            {
                "city": _string("City name."),
                "date": _string("Forecast date, YYYY-MM-DD."),
            },
            ["city", "date"],
        ),
    },
]

TOOLS_BY_NAME: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in TOOLS}


def tool_schemas(names: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    """Return Anthropic tool definitions for the named tools, in order."""

    schemas: list[dict[str, Any]] = []
    for name in names:
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            raise KeyError(f"unknown multi-step tool {name!r}")
        schemas.append(
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": tool["input_schema"],
            }
        )
    return schemas


__all__ = ["TOOLS", "TOOLS_BY_NAME", "tool_schemas"]
