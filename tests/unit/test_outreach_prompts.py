"""Unit tests for outreach prompts module — src/prompts/outreach.py.

Validates that the prompt registry returns correct prompts for each channel,
enforces anti-pattern rules, and rejects unsupported channels.
"""

from __future__ import annotations

from typing import get_args

import pytest

from src.prompts.outreach import (
    EMAIL_OUTREACH_PROMPT,
    TELEGRAM_OUTREACH_PROMPT,
    ChannelType,
    get_outreach_prompt,
)

# ===========================================================================
# get_outreach_prompt — happy path
# ===========================================================================


class TestGetOutreachPromptEmail:
    """get_outreach_prompt("email") returns the email prompt."""

    def test_returns_email_prompt(self):
        result = get_outreach_prompt("email")
        assert result is EMAIL_OUTREACH_PROMPT

    def test_email_prompt_contains_email_channel_in_json(self):
        """Output format section must include '"channel": "email"'."""
        result = get_outreach_prompt("email")
        assert '"channel": "email"' in result


class TestGetOutreachPromptTelegram:
    """get_outreach_prompt("telegram") returns the Telegram prompt."""

    def test_returns_telegram_prompt(self):
        result = get_outreach_prompt("telegram")
        assert result is TELEGRAM_OUTREACH_PROMPT

    def test_telegram_prompt_contains_telegram_channel_in_json(self):
        """Output format section must include '"channel": "telegram"'."""
        result = get_outreach_prompt("telegram")
        assert '"channel": "telegram"' in result


# ===========================================================================
# get_outreach_prompt — error path
# ===========================================================================


class TestGetOutreachPromptInvalid:
    """Unsupported channel types must raise ValueError."""

    def test_whatsapp_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported outreach channel.*whatsapp"):
            get_outreach_prompt("whatsapp")  # type: ignore[arg-type]

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported outreach channel"):
            get_outreach_prompt("")  # type: ignore[arg-type]

    def test_none_raises_value_error(self):
        with pytest.raises(ValueError, match="Unsupported outreach channel"):
            get_outreach_prompt(None)  # type: ignore[arg-type]


# ===========================================================================
# Prompt content — anti-patterns
# ===========================================================================


class TestEmailPromptAntiPatterns:
    """EMAIL_OUTREACH_PROMPT must document forbidden patterns."""

    def test_mentions_anti_patterns_section(self):
        assert "Anti-Patterns" in EMAIL_OUTREACH_PROMPT

    def test_forbids_dear_hiring_manager(self):
        assert "Dear Hiring Manager" in EMAIL_OUTREACH_PROMPT

    def test_forbids_synergy(self):
        lower = EMAIL_OUTREACH_PROMPT.lower()
        assert "synergy" in lower

    def test_forbids_limited_spots(self):
        lower = EMAIL_OUTREACH_PROMPT.lower()
        assert "limited spots" in lower

    def test_forbids_hope_this_finds_you_well(self):
        assert "I hope this message finds you well" in EMAIL_OUTREACH_PROMPT

    def test_forbids_leverage(self):
        lower = EMAIL_OUTREACH_PROMPT.lower()
        assert "leverage" in lower


class TestTelegramPromptAntiPatterns:
    """TELEGRAM_OUTREACH_PROMPT must document forbidden patterns."""

    def test_mentions_anti_patterns_section(self):
        assert "Anti-Patterns" in TELEGRAM_OUTREACH_PROMPT

    def test_forbids_formal_greeting(self):
        assert "formal greeting" in TELEGRAM_OUTREACH_PROMPT.lower()

    def test_forbids_company_intro(self):
        """Must forbid the corporate-style self-introduction."""
        assert "представляю компанию" in TELEGRAM_OUTREACH_PROMPT

    def test_forbids_links_in_first_message(self):
        lower = TELEGRAM_OUTREACH_PROMPT.lower()
        assert "links" in lower or "link" in lower


# ===========================================================================
# Telegram prompt — short format
# ===========================================================================


class TestTelegramPromptShortFormat:
    """Telegram DM prompt must enforce short message format."""

    def test_mentions_two_three_sentences(self):
        assert "2-3 sentences" in TELEGRAM_OUTREACH_PROMPT

    def test_mentions_sixty_words(self):
        assert "60 words" in TELEGRAM_OUTREACH_PROMPT

    def test_no_subject_field(self):
        """Telegram output format must not include a subject field."""
        assert (
            'No "subject" field' in TELEGRAM_OUTREACH_PROMPT
            or "subject" not in TELEGRAM_OUTREACH_PROMPT.split("Output Format")[1].split('"channel"')[0]
        )


# ===========================================================================
# ChannelType literal
# ===========================================================================


class TestChannelTypeLiteral:
    """ChannelType must be Literal['email', 'telegram']."""

    def test_channel_type_has_email(self):
        args = get_args(ChannelType)
        assert "email" in args

    def test_channel_type_has_telegram(self):
        args = get_args(ChannelType)
        assert "telegram" in args

    def test_channel_type_has_exactly_two_values(self):
        args = get_args(ChannelType)
        assert len(args) == 2, f"Expected exactly 2 channel types, got {args}"

    def test_channel_type_values(self):
        args = get_args(ChannelType)
        assert set(args) == {"email", "telegram"}


# ===========================================================================
# Prompt structure consistency
# ===========================================================================


class TestPromptStructureConsistency:
    """Both prompts must share consistent structural elements."""

    def test_both_prompts_have_role_section(self):
        assert "# Role" in EMAIL_OUTREACH_PROMPT
        assert "# Role" in TELEGRAM_OUTREACH_PROMPT

    def test_both_prompts_have_tone_section(self):
        assert "# Tone" in EMAIL_OUTREACH_PROMPT
        assert "# Tone" in TELEGRAM_OUTREACH_PROMPT

    def test_both_prompts_have_output_format(self):
        assert "# Output Format" in EMAIL_OUTREACH_PROMPT
        assert "# Output Format" in TELEGRAM_OUTREACH_PROMPT

    def test_both_prompts_have_language_rules(self):
        assert "# Language Rules" in EMAIL_OUTREACH_PROMPT
        assert "# Language Rules" in TELEGRAM_OUTREACH_PROMPT

    def test_both_prompts_mention_neighbor_tone(self):
        """Both must reference the signature tone."""
        assert "сосед, не продавец" in EMAIL_OUTREACH_PROMPT
        assert "сосед, не продавец" in TELEGRAM_OUTREACH_PROMPT

    def test_email_prompt_has_subject_field(self):
        """Email output JSON must include a subject field."""
        assert '"subject"' in EMAIL_OUTREACH_PROMPT

    def test_telegram_prompt_lacks_subject_in_output(self):
        """Telegram output JSON must not include a subject field in the format block."""
        output_section = TELEGRAM_OUTREACH_PROMPT.split("# Output Format")[1]
        json_block = output_section.split("{")[1].split("}")[0]
        assert "subject" not in json_block

    def test_both_prompts_are_nonempty_strings(self):
        assert isinstance(EMAIL_OUTREACH_PROMPT, str) and len(EMAIL_OUTREACH_PROMPT) > 100
        assert isinstance(TELEGRAM_OUTREACH_PROMPT, str) and len(TELEGRAM_OUTREACH_PROMPT) > 100
