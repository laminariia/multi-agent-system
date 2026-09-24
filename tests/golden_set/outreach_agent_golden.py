"""Golden set data for the Outreach Agent (3 cases)."""

from __future__ import annotations

import json

from tests.golden_set.evaluator import GoldenCase, ValidationRule

CASES: list[GoldenCase] = [
    GoldenCase(
        name="restaurant",
        agent="outreach",
        input_data={
            "business_name": "Trattoria Bella",
            "business_type": "restaurant",
            "city": "Moscow",
            "contact_name": "Alexei",
        },
        expected_output=json.dumps(
            {
                "subject": "A website for Trattoria Bella -- free consultation",
                "body": (
                    "Hi Alexei,\n\n"
                    "I noticed that Trattoria Bella in Moscow doesn't have a website yet. "
                    "As a restaurant, having an online presence helps customers find your menu "
                    "and hours.\n\n"
                    "We specialize in simple, effective websites for local businesses. "
                    "I'd love to offer a free 15-minute consultation.\n\n"
                    "Would you be open to a quick chat this week?\n\n"
                    "Best regards,\nMAS Team"
                ),
            }
        ),
        validators=[
            ValidationRule("has_keys", {"keys": ["subject", "body"]}),
            ValidationRule("must_mention", {"keywords": ["Trattoria Bella"]}),
            ValidationRule("must_not_mention", {"keywords": ["limited time offer", "act now", "spam"]}),
            ValidationRule("length_range", {"field": "body", "min": 20, "max": 150}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="local_shop",
        agent="outreach",
        input_data={
            "business_name": "Green Thumb Garden Center",
            "business_type": "retail",
            "city": "Saint Petersburg",
            "contact_name": "Maria",
            "has_social_media": True,
        },
        expected_output="```json\n"
        + json.dumps(
            {
                "subject": "Take Green Thumb Garden Center online",
                "body": (
                    "Hi Maria,\n\n"
                    "I see Green Thumb Garden Center has a social media presence but no website. "
                    "A dedicated site can showcase your products 24/7 and capture search traffic.\n\n"
                    "We build affordable websites for retail businesses. "
                    "Happy to chat about options.\n\n"
                    "Best,\nMAS Team"
                ),
            },
            indent=2,
        )
        + "\n```",
        validators=[
            ValidationRule("has_keys", {"keys": ["subject", "body"]}),
            ValidationRule("must_mention", {"keywords": ["Green Thumb"]}),
            ValidationRule("must_not_mention", {"keywords": ["buy now", "urgent"]}),
            ValidationRule("length_range", {"field": "body", "min": 20, "max": 150}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
    GoldenCase(
        name="professional_service",
        agent="outreach",
        input_data={
            "business_name": "Dr. Petrov Dental",
            "business_type": "dentist",
            "city": "Kazan",
            "contact_name": "Dr. Petrov",
        },
        expected_output=json.dumps(
            {
                "subject": "Online presence for Dr. Petrov Dental",
                "body": (
                    "Dear Dr. Petrov,\n\n"
                    "Patients increasingly search online for dental services. "
                    "A professional website for Dr. Petrov Dental in Kazan can help new patients "
                    "find and trust your practice.\n\n"
                    "We'd be happy to offer a free consultation.\n\n"
                    "Kind regards,\nMAS Team"
                ),
            }
        ),
        validators=[
            ValidationRule("has_keys", {"keys": ["subject", "body"]}),
            ValidationRule("must_mention", {"keywords": ["Petrov"]}),
            ValidationRule("must_not_mention", {"keywords": ["click here", "limited offer"]}),
            ValidationRule("length_range", {"field": "body", "min": 20, "max": 150}),
            ValidationRule("pydantic_valid", {}),
        ],
    ),
]
