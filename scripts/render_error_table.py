"""
Generate the README's § Error contract section from cqr.api.ERROR_RESPONSES.

Run this to update the README when the matrix changes:

    python scripts/render_error_table.py > /tmp/errors.md
    # then paste between the <!-- ERROR-TABLE-START --> markers.

tests/test_error_contract.py::test_render_matches_readme asserts the pasted
section is byte-identical to this script's output, so the README can't drift.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cqr.api import ERROR_RESPONSES  # noqa: E402


# Every (error_type, status) pair the app can emit -> what the client should
# do about it. Keyed by (status, error_type) because 422 has two occupants.
CLIENT_ACTIONS: dict[tuple[int, str], tuple[bool, str, str]] = {
    (413, "PayloadTooLarge"):     (False, "Request body exceeded `CQR_MAX_BODY_BYTES`.",
                                          "Split the batch or trim the transcript."),
    (422, "HTTPValidationError"): (False, "Request body failed schema validation (FastAPI's native `{detail: [...]}` shape).",
                                          "Fix the request body and resend."),
    (422, "TranscriptRejected"):  (False, "Provider accepted the request but rejected this transcript (too long, content policy).",
                                          "Send the transcript to human review, or trim/redact and resubmit."),
    (429, "QueueFull"):           (True,  "In-process job queue at capacity.",
                                          "Back off `Retry-After` seconds and resubmit."),
    (500, "JudgeOutputInvalid"):  (False, "Provider replied, but JSON never validated after output retries.",
                                          "Send the transcript to human review; the model can't self-correct."),
    (502, "JudgeRejected"):       (False, "Provider refused authoritatively — bad key, wrong model, permission denied.",
                                          "Fix credentials/config and rerun; batch aborts, CLI exits 3."),
    (503, "JudgeUnavailable"):    (True,  "Transient upstream failure — timeout, rate limit, connection drop.",
                                          "Retry after `Retry-After` seconds; resubmit `errors[].transcript_id` where `retryable`."),
    (404, "NotFound"):            (False, "No resource with that id (may have been LRU-evicted from the job table).",
                                          "Check the id; if it's a job, it may have aged out of `CQR_MAX_JOBS_KEPT`."),
}


def _declared_pairs() -> set[tuple[int, str]]:
    """Every (status, error_type) that any route declares. 422 is compound
    (validation OR TranscriptRejected); we surface both rows."""
    out: set[tuple[int, str]] = set()
    for _, statuses in ERROR_RESPONSES.items():
        for status in statuses:
            if status == 422:
                out.add((422, "HTTPValidationError"))
                out.add((422, "TranscriptRejected"))
            elif status == 413:
                out.add((413, "PayloadTooLarge"))
            elif status == 429:
                out.add((429, "QueueFull"))
            elif status == 500:
                out.add((500, "JudgeOutputInvalid"))
            elif status == 502:
                out.add((502, "JudgeRejected"))
            elif status == 503:
                out.add((503, "JudgeUnavailable"))
            elif status == 404:
                out.add((404, "NotFound"))
    return out


def render() -> str:
    rows = sorted(_declared_pairs())
    lines: list[str] = []
    lines.append("### Status × error_type")
    lines.append("")
    lines.append("| Status | `error_type` | Retryable | Meaning | Client action |")
    lines.append("|---|---|---|---|---|")
    for status, et in rows:
        retryable, meaning, action = CLIENT_ACTIONS[(status, et)]
        lines.append(f"| **{status}** | `{et}` | {'✓' if retryable else '✗'} | {meaning} | {action} |")
    lines.append("")
    lines.append("### Body shape")
    lines.append("")
    lines.append("Every non-2xx serializes to `ErrorBody`, **except** FastAPI's own 422 for body validation, which keeps its native shape. Callers on 422 must branch on the JSON shape.")
    lines.append("")
    lines.append("```jsonc")
    lines.append("// ErrorBody — used by 404, 413, 422 (TranscriptRejected), 429, 500, 502, 503")
    lines.append("{")
    lines.append('  "error_type": "JudgeUnavailable",')
    lines.append('  "message": "RateLimitError: 429 from provider",')
    lines.append('  "retryable": true,')
    lines.append('  "attempts": 2,')
    lines.append('  "retry_after_s": 30')
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("```jsonc")
    lines.append("// FastAPI's native 422 (body validation) — different shape")
    lines.append("{")
    lines.append('  "detail": [')
    lines.append('    {"loc": ["body", "turns"], "msg": "list should have at least 1 item", "type": "too_short"}')
    lines.append("  ]")
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("### Job semantics")
    lines.append("")
    lines.append("- `status: completed` may still carry per-item `errors[]`. The batch as a whole is done; check each entry's `error_type` and `retryable` to decide what to resubmit.")
    lines.append("- `status: failed` means a **config-scope** error (JudgeRejected) aborted the job — every remaining item would fail identically. Fix the config, then resubmit the batch fresh.")
    lines.append("- `error_type: CircuitOpen` items are marked non-retryable in the error record itself, but the underlying failures were retryable — the circuit latched after `CQR_CIRCUIT_THRESHOLD` consecutive retryable failures. Resubmit them once the upstream is healthy.")
    lines.append("- To resubmit only what failed:")
    lines.append("")
    lines.append("  ```bash")
    lines.append("  # ids of retryable per-item failures from a completed job")
    lines.append('  curl -s http://127.0.0.1:8000/jobs/$JOB \\')
    lines.append('    | jq -r \'.errors[] | select(.retryable) | .transcript_id\'')
    lines.append("  ```")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover
    sys.stdout.write(render())
