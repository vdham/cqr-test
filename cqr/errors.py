"""
Classified judge errors.

Every `Judge` implementation raises one of these — never a bare Exception —
so the API, the JobRunner, and the CLI can decide what to do with a failure
without inspecting exception text or a specific provider's exception tree.

Fields carried on every JudgeError:

- `retryable`: whether a resubmit of the SAME transcript could plausibly
  succeed later. Drives the JobRunner's circuit-breaker counter and the
  API's `Retry-After` header.
- `http_status`: what HTTP status the API returns when this bubbles up to
  a request handler.
- `scope`: `"transcript"` (bad content, invalid output — one item to
  quarantine) or `"config"` (bad key, wrong model, broken base URL — the
  whole run should stop; each item would repeat the same failure).
- `attempts`: how many provider calls the judge made before giving up on
  this transcript.

The four concrete subclasses cover the axes {retryable × scope} plus a
special one for "the provider replied but the JSON was still invalid after
retries".
"""
from __future__ import annotations


class JudgeError(Exception):
    """Base class. Callers should catch `JudgeError` if they want to react to
    any classified failure, or a specific subclass for finer control."""
    retryable: bool = False
    http_status: int = 500
    scope: str = "transcript"

    def __init__(self, message: str, attempts: int = 1):
        super().__init__(message)
        self.attempts = attempts


class JudgeUnavailable(JudgeError):
    """Transient upstream failure (timeout, connection drop, rate limit,
    provider 5xx). Retryable, transcript-scope: another attempt with the same
    input has a real chance of working."""
    retryable = True
    http_status = 503
    scope = "transcript"


class JudgeRejected(JudgeError):
    """The provider refused authoritatively — bad key, wrong model,
    permission denied. Not retryable; scope is `config` because every
    subsequent item in the same batch would fail identically, so the whole
    job should abort."""
    retryable = False
    http_status = 502
    scope = "config"


class TranscriptRejected(JudgeError):
    """The provider accepted the request but rejected this specific transcript
    (too long, content policy hit, malformed prompt input). Terminal for this
    item; other items in the same batch may still succeed."""
    retryable = False
    http_status = 422
    scope = "transcript"


class JudgeOutputInvalid(JudgeError):
    """Provider replied N times but the JSON never validated against the
    `Review` schema. Not retryable at the request layer (we've already
    burned the output-retry budget). Terminal for this item."""
    retryable = False
    http_status = 500
    scope = "transcript"


# --------------------------------------------------------- non-judge errors ----
# These aren't judge failures — they come from the queue, routing, or the
# request body — so they don't extend JudgeError. They share the ErrorBody
# response shape via the api.py exception handlers.


class QueueFull(Exception):
    """The in-process job queue is at capacity; the caller should back off
    and resubmit. Retryable; the API returns 429 with `Retry-After`."""
    http_status = 429
    retryable = True


class NotFoundError(Exception):
    """A referenced job or review id doesn't exist (may have been LRU-evicted
    from the job table, or was never submitted). Not retryable."""
    http_status = 404
    retryable = False


class PayloadTooLarge(Exception):
    """Request body exceeded `CQR_MAX_BODY_BYTES`. Not retryable — the client
    must split the batch or trim the transcript. Enforced by an ASGI
    middleware before the body is parsed."""
    http_status = 413
    retryable = False
