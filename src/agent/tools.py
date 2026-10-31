"""Anthropic tool schemas for the hand-written mini-set.

Phase 2 does **not** execute tools: there are no handlers here and the agent
never invents or replays a tool result. The model emits ``tool_use`` blocks;
the loop validates them against these schemas and the Phase-1 scorer scores
the final attempt.

These schemas are original to this repository. They are not copied from
xLAM and deliberately avoid every frozen held-out candidate tool in
``docs/CANDIDATES.md``.
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


def _boolean(description: str) -> dict[str, Any]:
    return {"type": "boolean", "description": description}


def _array(description: str, items: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"type": "array", "description": description, "items": items, **extra}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_weather",
        "description": "Current weather for one city.",
        "input_schema": _object(
            {
                "city": _string("City name."),
                "unit": {
                    "type": "string",
                    "enum": ["celsius", "fahrenheit"],
                    "description": "Temperature unit; defaults to celsius.",
                },
                "include_forecast": _boolean("Include a short forecast."),
            },
            ["city"],
        ),
    },
    {
        "name": "convert_units",
        "description": "Convert a numeric value between units.",
        "input_schema": _object(
            {
                "value": _number("Value to convert."),
                "from_unit": _string("Source unit."),
                "to_unit": _string("Target unit."),
            },
            ["value", "from_unit", "to_unit"],
        ),
    },
    {
        "name": "search_recipes",
        "description": "Search recipes by ingredients.",
        "input_schema": _object(
            {
                "ingredients": _array(
                    "Ingredients that must be used.",
                    _string("An ingredient."),
                    minItems=1,
                ),
                "max_results": _integer("Maximum recipes to return.", minimum=1),
                "vegetarian": _boolean("Restrict to vegetarian recipes."),
            },
            ["ingredients"],
        ),
    },
    {
        "name": "create_calendar_event",
        "description": "Create one calendar event.",
        "input_schema": _object(
            {
                "title": _string("Event title."),
                "start_time": _string("ISO-8601 start time."),
                "duration_minutes": _integer("Duration in minutes.", minimum=1),
                "attendees": _array("Attendee email addresses.", _string("Email.")),
                "location": {
                    "type": ["string", "null"],
                    "description": "Location, or null for none.",
                },
                "reminders": _array(
                    "Reminder offsets in minutes.",
                    _integer("Minutes before the event.", minimum=0),
                ),
            },
            ["title", "start_time", "duration_minutes"],
        ),
    },
    {
        "name": "send_email",
        "description": "Send an email.",
        "input_schema": _object(
            {
                "to": _array(
                    "Recipient addresses.",
                    _string("Email address."),
                    minItems=1,
                ),
                "subject": _string("Subject line."),
                "body": _string("Plain-text body."),
                "cc": _array("Cc addresses.", _string("Email address.")),
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "Message priority.",
                },
            },
            ["to", "subject", "body"],
        ),
    },
    {
        "name": "calculate",
        "description": "Evaluate a mathematical expression.",
        "input_schema": _object(
            {
                "expression": _string("Expression to evaluate."),
                "precision": _integer(
                    "Decimal places in the result.",
                    minimum=0,
                    maximum=10,
                ),
            },
            ["expression"],
        ),
    },
    {
        "name": "lookup_book",
        "description": "Look up bibliographic information for a book.",
        "input_schema": _object(
            {
                "title": _string("Book title."),
                "author": _string("Author name."),
                "year": _integer("Publication year."),
            },
            ["title"],
        ),
    },
    {
        "name": "translate_text",
        "description": "Translate text into a target language.",
        "input_schema": _object(
            {
                "text": _string("Text to translate."),
                "target_language": _string("Target language."),
                "source_language": _string("Source language, if known."),
                "formality": {
                    "type": "string",
                    "enum": ["formal", "informal"],
                    "description": "Register to use.",
                },
            },
            ["text", "target_language"],
        ),
    },
    {
        "name": "get_stock_quote",
        "description": "Latest quote for a stock ticker.",
        "input_schema": _object(
            {
                "ticker": _string("Ticker symbol."),
                "include_history": _boolean("Include recent price history."),
                "history_days": _integer(
                    "History window in days.",
                    minimum=1,
                    maximum=365,
                ),
            },
            ["ticker"],
        ),
    },
    {
        "name": "play_music",
        "description": "Play a song.",
        "input_schema": _object(
            {
                "song": _string("Song title."),
                "artist": _string("Artist name."),
                "volume": _integer("Volume from 0 to 100.", minimum=0, maximum=100),
            },
            ["song"],
        ),
    },
    {
        "name": "set_thermostat",
        "description": "Set a thermostat and an optional schedule.",
        "input_schema": _object(
            {
                "target_temperature": _number("Target temperature in celsius."),
                "mode": {
                    "type": "string",
                    "enum": ["heat", "cool", "auto", "off"],
                    "description": "Thermostat mode.",
                },
                "schedule": _array(
                    "Scheduled setpoints.",
                    _object(
                        {
                            "time": _string("Local time, HH:MM."),
                            "temperature": _number("Temperature in celsius."),
                        },
                        ["time", "temperature"],
                    ),
                ),
            },
            ["target_temperature", "mode"],
        ),
    },
    {
        "name": "create_playlist",
        "description": "Create a music playlist.",
        "input_schema": _object(
            {
                "name": _string("Playlist name."),
                "tracks": _array(
                    "Ordered playlist tracks.",
                    _object(
                        {
                            "title": _string("Track title."),
                            "artist": _string("Track artist."),
                            "duration_seconds": _integer(
                                "Track length in seconds.",
                                minimum=1,
                            ),
                        },
                        ["title"],
                    ),
                    minItems=1,
                ),
                "is_public": _boolean("Whether the playlist is public."),
            },
            ["name", "tracks"],
        ),
    },
    {
        "name": "get_restaurant_menu",
        "description": "Fetch a restaurant menu.",
        "input_schema": _object(
            {
                "restaurant": _string("Restaurant name."),
                "include_prices": _boolean("Include prices in the menu."),
                "dietary_filters": _array(
                    "Dietary filters to apply.",
                    _string("A dietary filter."),
                ),
            },
            ["restaurant"],
        ),
    },
    {
        "name": "book_flight",
        "description": "Book a flight itinerary.",
        "input_schema": _object(
            {
                "origin": _string("Origin airport or city."),
                "destination": _string("Destination airport or city."),
                "departure_date": _string("Departure date, YYYY-MM-DD."),
                "passengers": _integer("Number of passengers.", minimum=1),
                "seat_preference": {
                    "type": ["string", "null"],
                    "description": "Seat preference, or null for none.",
                },
                "flexible_dates": _boolean("Allow flexible dates."),
            },
            ["origin", "destination", "departure_date", "passengers"],
        ),
    },
    {
        "name": "calculate_tip",
        "description": "Calculate a tip and optional split.",
        "input_schema": _object(
            {
                "bill_amount": _number("Bill amount."),
                "tip_percent": _number("Tip percentage."),
                "split_ways": _integer("Number of ways to split.", minimum=1),
            },
            ["bill_amount", "tip_percent"],
        ),
    },
    {
        "name": "get_time",
        "description": "Get the current time, optionally in a timezone.",
        "input_schema": _object(
            {"timezone": _string("IANA timezone name.")},
            [],
        ),
    },
    {
        "name": "roll_dice",
        "description": "Roll one or more dice.",
        "input_schema": _object(
            {
                "sides": _integer("Sides per die.", minimum=2, default=6),
                "count": _integer(
                    "Number of dice.",
                    minimum=1,
                    maximum=10,
                    default=1,
                ),
            },
            [],
        ),
    },
    {
        "name": "fetch_news",
        "description": "Fetch recent news items about a topic.",
        "input_schema": _object(
            {
                "topic": _string("News topic."),
                "limit": _integer("Maximum items.", minimum=1, maximum=50),
            },
            ["topic"],
        ),
    },
    {
        "name": "update_inventory",
        "description": "Update the stock level for one SKU.",
        "input_schema": _object(
            {
                "sku": _string("Stock keeping unit."),
                "quantity": _integer("New quantity on hand.", minimum=0),
                "note": {
                    "type": ["string", "null"],
                    "description": "Optional note, or null.",
                },
                "tags": _array("Tags for the update.", _string("A tag.")),
            },
            ["sku", "quantity"],
        ),
    },
    {
        "name": "summarize_text",
        "description": "Summarize a passage of text.",
        "input_schema": _object(
            {
                "text": _string("Text to summarize."),
                "max_sentences": _integer("Maximum sentences.", minimum=1),
                "style": {
                    "type": "string",
                    "enum": ["bullet", "paragraph"],
                    "description": "Summary style.",
                },
            },
            ["text"],
        ),
    },
]

TOOLS_BY_NAME: dict[str, dict[str, Any]] = {tool["name"]: tool for tool in TOOLS}


def tool_schemas() -> list[dict[str, Any]]:
    """Anthropic API tool definitions (schemas only; no handlers)."""

    return [
        {key: tool[key] for key in ("name", "description", "input_schema")}
        for tool in TOOLS
    ]
