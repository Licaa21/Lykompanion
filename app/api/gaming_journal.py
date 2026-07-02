"""Gaming Journal: the per-game view of everything the companion knows — game-scope memories,
playthrough profiles (sessions) with their session-scope memories, and each profile's unconfirmed
screen observations. Powers the Gaming Journal modal (sidebar, next to Personal Data)."""

from fastapi import APIRouter, HTTPException

from app.core import game_state, game_state_processes, memory, observations
from app.models.schemas import GamingJournalGame, GamingJournalSession

router = APIRouter(tags=["gaming-journal"])


@router.get("/api/gaming-journal", response_model=list[GamingJournalGame])
async def get_gaming_journal() -> list[GamingJournalGame]:
    memories = memory.load_memories()
    whitelist = game_state_processes.load_whitelist()

    # Every game we know anything about: approved for tracking, has sessions, or has memories.
    processes: dict[str, str] = {}
    for proc in whitelist:
        processes.setdefault(proc.lower(), proc)
    for proc in game_state.get_processes_with_sessions():
        processes.setdefault(proc.lower(), proc)
    for m in memories:
        if m.get("process"):
            processes.setdefault(m["process"].lower(), m["process"])

    whitelist_lower = {p.lower() for p in whitelist}
    games = []
    for key, proc in sorted(processes.items()):
        game_memories = [
            m for m in memories
            if (m.get("process") or "").lower() == key and not m.get("session_id")
        ]
        active_id = game_state.peek_active_session_id(proc)
        sessions = []
        for s in game_state.get_sessions(proc):
            session_memories = [
                m for m in memories
                if (m.get("process") or "").lower() == key and m.get("session_id") == s["session_id"]
            ]
            sessions.append(GamingJournalSession(
                session_id=s["session_id"],
                name=s["name"],
                updated_at=s.get("updated_at"),
                active=s["session_id"] == active_id,
                memories=session_memories,
                observations=observations.get_observations(proc, s["session_id"]),
            ))
        games.append(GamingJournalGame(
            process=proc,
            tracked=key in whitelist_lower,
            memories=game_memories,
            sessions=sessions,
        ))
    return games


@router.delete("/api/observations/{observation_id}")
async def delete_observation(observation_id: str) -> dict:
    if not observations.remove_observations([observation_id]):
        raise HTTPException(status_code=404, detail="Observation not found.")
    return {"ok": True}
