"""Customer reviews: list them and reply. Post-launch/ops, not part of the release pipeline.

Implemented against Apple's documented ASC OpenAPI schema — see testflight.py's module docstring
for the same "not yet confirmed live" caveat that applies here too.
"""

from __future__ import annotations

from typing import Any

from asogenic import context
from asogenic.errors import validation_error


def list_customer_reviews(*, app_id: str, territory: str | None = None) -> dict[str, Any]:
    """`response_id` on each review is null if nobody has replied yet — lets a caller filter to
    unanswered reviews without a second call per review."""
    client = context.get_asc_client()
    numeric_app_id = client.find_app_by_bundle_id(app_id).id
    reviews = client.list_customer_reviews(numeric_app_id, territory=territory)
    return {
        "reviews": [
            {
                "id": r.id,
                "rating": r.rating,
                "title": r.title,
                "body": r.body,
                "reviewer_nickname": r.reviewer_nickname,
                "territory": r.territory,
                "created_date": r.created_date,
                "response_id": r.response_id,
            }
            for r in reviews
        ]
    }


def reply_to_review(*, review_id: str, response_body: str) -> dict[str, Any]:
    """Apple's API has no PATCH for customerReviewResponses — CREATE and DELETE are the only
    operations it allows. Replying to a review that already has one fails with Apple's own
    ENTITY_ERROR (a response relationship can't have two children); call
    asogenic_delete_review_response first (asogenic_list_customer_reviews' `response_id` tells you
    it exists), then reply again, to edit a reply."""
    if not response_body.strip():
        raise validation_error("response_body must not be empty", field_name="response_body")
    client = context.get_asc_client()
    response_id = client.reply_to_review(review_id, response_body=response_body.strip())
    return {"id": response_id}


def delete_review_response(*, response_id: str) -> dict[str, Any]:
    client = context.get_asc_client()
    client.delete_review_response(response_id)
    return {"deleted": True}
