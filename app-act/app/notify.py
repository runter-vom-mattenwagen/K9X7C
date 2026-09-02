"""ACT System — ntfy Notifications"""

import os
import httpx
from typing import Optional

NTFY_URL = os.environ.get("ACT_NTFY_URL", "https://ntfy.sh")
NTFY_TOPIC = os.environ.get("ACT_NTFY_TOPIC", "act")
ACT_BASE_URL = os.environ.get("ACT_BASE_URL", "https://act.claude.int")
NTFY_ENABLED = os.environ.get("ACT_NTFY_ENABLED", "false").lower() == "true"


async def send_notification(
    title: str,
    message: str,
    priority: str = "default",
    tags: str = "ticket",
    click_url: Optional[str] = None
):
    """Send notification via ntfy JSON API (avoids header encoding issues with emojis)."""
    if not NTFY_ENABLED:
        return
    url = NTFY_URL
    payload = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": 4 if priority == "high" else 3,
        "tags": tags.split(","),
    }
    if click_url:
        payload["click"] = click_url

    try:
        async with httpx.AsyncClient(verify=False) as client:
            await client.post(url, json=payload)
    except Exception as e:
        print(f"[ntfy] Failed to send notification: {e}")


async def notify_new_ticket(ticket: dict, app_name: str):
    await send_notification(
        title=f"New {ticket['type']}: {ticket['title']}",
        message=f"App: {app_name}\nID: {ticket['id']}\nPriority: {ticket['priority']}",
        tags="ticket,new",
        click_url=f"{ACT_BASE_URL}/tickets/{ticket['id']}"
    )


async def notify_proposal(ticket: dict, proposal: dict):
    await send_notification(
        title=f"Claude Proposal: {ticket['title']}",
        message=f"Ticket: {ticket['id']}\nProposal: {proposal['id']}\n\n{proposal['summary'][:200]}",
        tags="robot_face,ticket",
        priority="high",
        click_url=f"{ACT_BASE_URL}/tickets/{ticket['id']}"
    )


async def notify_status_change(ticket: dict, old_status: str, new_status: str, actor: str):
    await send_notification(
        title=f"{ticket['id']}: {old_status} -> {new_status}",
        message=f"{ticket['title']}\nBy: {actor}",
        tags="ticket,arrows_counterclockwise",
        click_url=f"{ACT_BASE_URL}/tickets/{ticket['id']}"
    )
