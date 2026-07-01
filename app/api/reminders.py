from fastapi import APIRouter, HTTPException

from app.core import reminders
from app.models.schemas import AlarmEntry, PendingNotification, ReminderEntry

router = APIRouter(prefix="/api/reminders", tags=["reminders"])


@router.get("", response_model=list[ReminderEntry])
async def list_reminders() -> list[ReminderEntry]:
    return reminders.list_reminders()


@router.delete("/{reminder_id}")
async def delete_reminder(reminder_id: str) -> dict:
    removed = reminders.remove_reminder(reminder_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Reminder not found.")
    return {"ok": True}


@router.get("/alarms", response_model=list[AlarmEntry])
async def list_alarms() -> list[AlarmEntry]:
    return reminders.list_alarms()


@router.delete("/alarms/{alarm_id}")
async def delete_alarm(alarm_id: str) -> dict:
    removed = reminders.cancel_alarm(alarm_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Alarm not found.")
    return {"ok": True}


@router.get("/pending", response_model=list[PendingNotification])
async def list_pending() -> list[PendingNotification]:
    return reminders.load_pending()


@router.delete("/pending/{pending_id}")
async def ack_pending(pending_id: str) -> dict:
    reminders.remove_pending(pending_id)
    return {"ok": True}
