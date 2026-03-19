"""Warmup email templates organized by stage.

Each stage has multiple template variations to avoid repetitive content
and improve deliverability.  Early stages (Week 1-2) use personal,
reply-worthy content.  Later stages (Week 5-6) transition to business
outreach templates.

Templates support ``{{first_name}}``, ``{{company}}``, and ``{{city}}``
placeholders for personalization (used in Week 3+).  These match the
``personalize_template()`` format in ``email_sender.py``.

Usage::

    from src.enrichment.warmup_templates import get_warmup_template
    from src.enrichment.warmup import WarmupStage
    from src.enrichment.email_sender import personalize_template

    tmpl = get_warmup_template(WarmupStage.WEEK_3)
    body = personalize_template(tmpl["body"], {"first_name": "John", "company": "Acme", "city": "Austin"})
"""

from __future__ import annotations

import random

from src.enrichment.warmup import WarmupStage

# ---------------------------------------------------------------------------
# Template definitions per stage
# ---------------------------------------------------------------------------

WARMUP_TEMPLATES: dict[WarmupStage, list[dict[str, str]]] = {
    # ------------------------------------------------------------------
    # Week 1: Personal contacts, reply-worthy conversations
    # ------------------------------------------------------------------
    WarmupStage.WEEK_1: [
        {
            "subject": "Quick question about your experience",
            "body": (
                "Hi there,\n\n"
                "I've been exploring some new tools for project management "
                "and was curious if you've tried anything recently that stood out.\n\n"
                "Would love to hear your thoughts if you have a moment.\n\n"
                "Best regards"
            ),
        },
        {
            "subject": "Following up on our last conversation",
            "body": (
                "Hey,\n\n"
                "I was thinking about what we discussed last time and wanted "
                "to share a few ideas that came to mind.\n\n"
                "Let me know when you have a chance to chat -- "
                "no rush at all.\n\n"
                "Cheers"
            ),
        },
        {
            "subject": "Article I thought you might enjoy",
            "body": (
                "Hi,\n\n"
                "I came across an interesting article about industry trends "
                "and immediately thought of you. It covers some of the "
                "challenges we were talking about.\n\n"
                "Happy to discuss if you find it useful.\n\n"
                "Talk soon"
            ),
        },
    ],
    # ------------------------------------------------------------------
    # Week 2: Personal + seed list, test reply chains
    # ------------------------------------------------------------------
    WarmupStage.WEEK_2: [
        {
            "subject": "Thoughts on the latest updates",
            "body": (
                "Hi,\n\n"
                "Have you seen the recent changes in our field? "
                "I noticed a few shifts that could impact how we approach "
                "things going forward.\n\n"
                "Would be great to get your perspective on this.\n\n"
                "Best"
            ),
        },
        {
            "subject": "Coffee catch-up soon?",
            "body": (
                "Hey,\n\n"
                "It's been a while since we connected. I'd love to grab "
                "coffee (virtual or in-person) and hear what you've been "
                "working on.\n\n"
                "Any day next week work for you?\n\n"
                "Looking forward to it"
            ),
        },
        {
            "subject": "Resource you might find helpful",
            "body": (
                "Hi,\n\n"
                "I recently put together a summary of the best practices "
                "I've picked up this quarter. Nothing fancy, just practical "
                "notes that might save you some time.\n\n"
                "Let me know if you'd like me to send it over.\n\n"
                "Cheers"
            ),
        },
    ],
    # ------------------------------------------------------------------
    # Week 3: Mixed (70% personal, 30% warm leads)
    # ------------------------------------------------------------------
    WarmupStage.WEEK_3: [
        {
            "subject": "A quick introduction",
            "body": (
                "Hi {{first_name}},\n\n"
                "I came across {{company}} while researching businesses in "
                "{{city}} and was impressed by what you're doing.\n\n"
                "I'd love to learn more about your current challenges "
                "and see if there's any way I can help.\n\n"
                "Would you be open to a brief chat?\n\n"
                "Best regards"
            ),
        },
        {
            "subject": "Reaching out from a fellow professional",
            "body": (
                "Hi {{first_name}},\n\n"
                "I work with businesses similar to {{company}} and "
                "wanted to introduce myself. We share some common ground "
                "in {{city}} and I think there could be a good fit.\n\n"
                "No pressure at all -- just wanted to say hello.\n\n"
                "Warm regards"
            ),
        },
    ],
    # ------------------------------------------------------------------
    # Week 4: Mixed (50% personal, 50% cold verified)
    # ------------------------------------------------------------------
    WarmupStage.WEEK_4: [
        {
            "subject": "Idea for {{company}}",
            "body": (
                "Hi {{first_name}},\n\n"
                "I noticed {{company}} has been growing in {{city}} -- "
                "congrats on the progress.\n\n"
                "I've been working on a few projects that might align "
                "with what you're building. Would you be interested in "
                "a quick 10-minute call to explore?\n\n"
                "Either way, keep up the great work.\n\n"
                "Best"
            ),
        },
        {
            "subject": "Quick thought for your team",
            "body": (
                "Hi {{first_name}},\n\n"
                "I've been researching companies in {{city}} and "
                "{{company}} caught my eye. I have a couple of ideas "
                "that could help with your online presence.\n\n"
                "Happy to share them if you're interested -- "
                "no strings attached.\n\n"
                "Cheers"
            ),
        },
    ],
    # ------------------------------------------------------------------
    # Week 5: Mostly cold (30% personal, 70% cold)
    # ------------------------------------------------------------------
    WarmupStage.WEEK_5: [
        {
            "subject": "Website opportunity for {{company}}",
            "body": (
                "Hi {{first_name}},\n\n"
                "I help businesses in {{city}} improve their web presence "
                "and attract more customers online.\n\n"
                "After taking a look at {{company}}, I noticed a few areas "
                "where small improvements could make a real difference. "
                "I'd be happy to share my observations.\n\n"
                "Would a brief conversation work for you this week?\n\n"
                "Best regards"
            ),
        },
        {
            "subject": "Growing your business online",
            "body": (
                "Hi {{first_name}},\n\n"
                "I work with local businesses like {{company}} to build "
                "stronger digital foundations -- better websites, "
                "improved search visibility, and more leads.\n\n"
                "If this is something your team is thinking about, "
                "I'd love to share a few ideas.\n\n"
                "No commitment -- just a friendly conversation.\n\n"
                "Cheers"
            ),
        },
        {
            "subject": "A service that might interest {{company}}",
            "body": (
                "Hi {{first_name}},\n\n"
                "I specialize in helping {{city}}-based businesses "
                "strengthen their online presence. My team has worked "
                "with companies similar to {{company}} and delivered "
                "measurable results.\n\n"
                "Would you be open to a quick chat about what we could "
                "do for you?\n\n"
                "Looking forward to hearing from you"
            ),
        },
    ],
    # ------------------------------------------------------------------
    # Week 6: Full cold outreach (production templates)
    # ------------------------------------------------------------------
    WarmupStage.WEEK_6: [
        {
            "subject": "Web project proposal for {{company}}",
            "body": (
                "Hi {{first_name}},\n\n"
                "I lead a development team that builds high-quality "
                "websites and web applications for businesses in {{city}}.\n\n"
                "After reviewing {{company}}, I believe we could help you:\n"
                "- Modernize your website design\n"
                "- Improve page load speed and SEO\n"
                "- Add features that convert visitors into customers\n\n"
                "I'd love to schedule a brief call to discuss your goals. "
                "Would any day this week work?\n\n"
                "Best regards"
            ),
        },
        {
            "subject": "How we help businesses like {{company}}",
            "body": (
                "Hi {{first_name}},\n\n"
                "My team specializes in building modern web solutions "
                "for growing businesses. We've helped companies in {{city}} "
                "increase their online presence and generate more leads.\n\n"
                "I'd like to offer a complimentary review of {{company}}'s "
                "current website with specific, actionable suggestions.\n\n"
                "Would you be interested?\n\n"
                "Looking forward to connecting"
            ),
        },
        {
            "subject": "Partnership opportunity with {{company}}",
            "body": (
                "Hi {{first_name}},\n\n"
                "I'm reaching out because I think there's a great fit "
                "between our development team and {{company}}.\n\n"
                "We offer:\n"
                "- Custom website development\n"
                "- Mobile-responsive design\n"
                "- Ongoing support and maintenance\n\n"
                "Our recent project for a similar business in {{city}} "
                "resulted in a 40% increase in online inquiries.\n\n"
                "Can I share more details?\n\n"
                "Best"
            ),
        },
    ],
}


def get_warmup_template(
    stage: WarmupStage,
    *,
    seed: int | None = None,
) -> dict[str, str]:
    """Return a random template for the given warmup stage.

    Parameters
    ----------
    stage:
        The current warmup stage.
    seed:
        Optional RNG seed for reproducible template selection (useful in
        tests and deterministic replay scenarios).

    Returns
    -------
    dict
        A dict with ``"subject"`` and ``"body"`` keys.

    Raises
    ------
    ValueError
        If ``stage`` is ``PRODUCTION`` (no warmup templates needed).
    """
    templates = WARMUP_TEMPLATES.get(stage)
    if templates is None:
        msg = f"No warmup templates for stage {stage.value}. Production uses real outreach templates."
        raise ValueError(msg)

    rng = random.Random(seed)  # noqa: S311
    return rng.choice(templates)
