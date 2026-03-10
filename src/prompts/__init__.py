"""System prompts for all MAS agents."""

from src.prompts.bid import BID_SYSTEM_PROMPT
from src.prompts.content import CONTENT_SYSTEM_PROMPT
from src.prompts.critic import CRITIC_SYSTEM_PROMPT
from src.prompts.design import DESIGN_SYSTEM_PROMPT
from src.prompts.dev import DEV_SYSTEM_PROMPT
from src.prompts.outreach import EMAIL_OUTREACH_PROMPT, TELEGRAM_OUTREACH_PROMPT, get_outreach_prompt
from src.prompts.packager import PACKAGER_SYSTEM_PROMPT
from src.prompts.planner import PLANNER_SYSTEM_PROMPT
from src.prompts.scout import SCOUT_SYSTEM_PROMPT

__all__ = [
    "BID_SYSTEM_PROMPT",
    "CONTENT_SYSTEM_PROMPT",
    "CRITIC_SYSTEM_PROMPT",
    "DESIGN_SYSTEM_PROMPT",
    "DEV_SYSTEM_PROMPT",
    "EMAIL_OUTREACH_PROMPT",
    "PACKAGER_SYSTEM_PROMPT",
    "PLANNER_SYSTEM_PROMPT",
    "SCOUT_SYSTEM_PROMPT",
    "TELEGRAM_OUTREACH_PROMPT",
    "get_outreach_prompt",
]
