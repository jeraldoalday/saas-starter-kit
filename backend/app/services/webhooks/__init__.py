"""Outbound webhooks: telling a customer's system what happened in ours.

Four modules, split along the seams that actually differ:

* `signing` — the HMAC scheme. Pure functions, no database, no network; it
  is also the reference implementation a customer ports into their own
  receiver, which is why the verification half lives here despite nothing
  in the request path calling it.
* `urls` — whether a customer-supplied URL is safe to POST to. A security
  control rather than a feature: anywhere a customer names a URL the server
  will fetch, they have been handed a request from inside the network.
* `endpoints` — the registry an owner manages: create, list, rotate, delete.
* `dispatch` — turning one thing that happened into queued deliveries, and
  the worker half that sends them and decides what a failure means.

The split that matters is the last one. Dispatch runs inside the request
that caused the event and must be cheap and unfailing — it writes rows and
returns. Delivery runs out of process (`app/scripts/deliver_webhooks.py`),
talks to the open internet, and is allowed to be slow. Putting the POST in
the request path would mean a customer's unreachable receiver adding its
timeout to our own API's latency, which is how one party's outage becomes
both parties' outage.
"""

from app.services.webhooks.dispatch import (
    attempt_delivery,
    deliver_due,
    dispatch_event,
    purge_deliveries,
)
from app.services.webhooks.endpoints import (
    EndpointLimitExceededError,
    EndpointNotFoundError,
    active_endpoint_count,
    count_endpoints,
    create_endpoint,
    delete_endpoint,
    effective_endpoint_limit,
    get_endpoint,
    list_deliveries,
    list_endpoints,
    rotate_secret,
    set_endpoint_active,
)
from app.services.webhooks.signing import (
    canonical_body,
    compute_signature,
    delivery_headers,
    generate_secret,
    signature_header,
    verify_signature,
)
from app.services.webhooks.urls import UnsafeWebhookUrlError, validate_webhook_url

__all__ = [
    "EndpointLimitExceededError",
    "EndpointNotFoundError",
    "UnsafeWebhookUrlError",
    "active_endpoint_count",
    "attempt_delivery",
    "canonical_body",
    "compute_signature",
    "count_endpoints",
    "create_endpoint",
    "delete_endpoint",
    "deliver_due",
    "delivery_headers",
    "dispatch_event",
    "effective_endpoint_limit",
    "generate_secret",
    "get_endpoint",
    "list_deliveries",
    "list_endpoints",
    "purge_deliveries",
    "rotate_secret",
    "set_endpoint_active",
    "signature_header",
    "validate_webhook_url",
    "verify_signature",
]
