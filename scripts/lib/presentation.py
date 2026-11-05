"""Compact CLI presentation; transport/protocol results remain lossless internally."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from output_limits import OutputLimits, collect_text


DEFAULT_OUTPUT_BYTES = 16 * 1024


def compact_result(result: dict[str, Any], max_bytes: int = DEFAULT_OUTPUT_BYTES) -> dict[str, Any]:
    result = deepcopy(result)
    data = result.get("data") or {}
    # Keep stdout byte-for-byte unless bounded: callers may parse remote JSON.
    data.pop("command", None)
    data.pop("success", None)  # legacy wrapper duplicates the envelope
    if not data.get("stderr"):
        data.pop("stderr", None)
    if not data.get("warnings"):
        data.pop("warnings", None)
    output = data.get("output") or {}
    output = {key: value for key, value in output.items()
              if isinstance(value, dict) and value.get("truncated")}
    for key in ("stdout", "stderr"):
        if not isinstance(data.get(key), str):
            continue
        bounded = collect_text(data[key], OutputLimits(
            max_bytes=max_bytes, max_lines=4000, tail_bytes=max_bytes // 4,
        ))
        if bounded.truncated:
            data[key] = bounded.text
            previous = output.get(key, {})
            output[key] = {**bounded.to_meta(), **previous}
    if output:
        data["output"] = output
    else:
        data.pop("output", None)
    result["data"] = data
    result["meta"] = {key: value for key, value in (result.get("meta") or {}).items()
                      if value is not None and value != []}
    error = result.get("error")
    if error and data.get('error') == error.get('message'):
        data.pop('error', None)
    if error and error.get("message") == data.get("stderr"):
        error["message"] = "Operation failed; see data.stderr"
    return result
