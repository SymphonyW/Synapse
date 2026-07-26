from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from app.events.models import AgentInfoEvent


def to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {
            str(field.name): to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }

    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}

    if isinstance(value, (str, bool)) or value is None:
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if isinstance(value, set):
        return [to_jsonable(item) for item in sorted(value, key=str)]

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_jsonable(item) for item in value]

    return str(value)


def serialize_legacy_info_event(event: AgentInfoEvent) -> str:
    envelope = {
        "schema": event.schema,
        "agent_event": event.event_type,
        "payload": to_jsonable(event.payload),
    }
    display_message = event.display_message.strip()
    if display_message:
        envelope["display_message"] = display_message

    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
