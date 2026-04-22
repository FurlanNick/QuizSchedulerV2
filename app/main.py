import os
import traceback
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.models import (
    SignInRequest, SignInResponse,
    SetupRequest, RosterRequest, TeamChangeRequest,
    GenerateMeetRequest, LockMeetRequest, ImportRequest,
    ProgramConfig, ProgramState,
    MatchupsRequest, MatchupsResponse, Matchup,
    ScheduleRequest, ScheduleResponse, ScheduleItem,
)
from app.session_store import get_or_create_session, get_session, save_session
from app.matchups import MatchupSolver
from app.scheduler import ScheduleSolver
from app.engine import generate_meets

app = FastAPI(
    title="Quiz Schedule Generator",
    description="Multi-meet quiz scheduling with team roster management",
    version="2.0.0",
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def read_index():
    return FileResponse(os.path.join("app/static", "index.html"))


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/signin", response_model=SignInResponse)
async def signin(req: SignInRequest):
    if not req.name.strip():
        raise HTTPException(400, "Name cannot be empty.")
    state, is_new = get_or_create_session(req.name)
    save_session(state)
    return SignInResponse(session_id=state.session_id, name=state.owner_name, is_new=is_new)


# ── Session state ─────────────────────────────────────────────────────────────

@app.get("/api/state/{session_id}")
async def get_state(session_id: str):
    state = get_session(session_id)
    if not state:
        raise HTTPException(404, "Session not found. Please sign in again.")
    return state.model_dump()


# ── Setup ─────────────────────────────────────────────────────────────────────

@app.post("/api/setup")
async def setup(req: SetupRequest):
    state = get_session(req.session_id)
    if not state:
        raise HTTPException(404, "Session not found.")

    # Only reset meets if core config changes
    changed = True
    if state.config:
        c1 = state.config
        c2 = req.config
        if (c1.n_quiz_meets == c2.n_quiz_meets and
            c1.n_rooms == c2.n_rooms and
            c1.n_time_slots == c2.n_time_slots and
            c1.matches_per_team == c2.matches_per_team and
            c1.tournament_type == c2.tournament_type):
            changed = False

    state.config = req.config
    if changed:
        state.meets = []
        state.team_changes = []

    save_session(state)
    return {"ok": True, "message": "Configuration saved."}


# ── Roster ────────────────────────────────────────────────────────────────────

@app.post("/api/roster")
async def set_roster(req: RosterRequest):
    state = get_session(req.session_id)
    if not state:
        raise HTTPException(404, "Session not found.")
    names = [n.strip() for n in req.teams if n.strip()]
    if len(names) < 3:
        raise HTTPException(400, "Need at least 3 teams.")

    # Check if the new list is a superset of the old one (ignoring order)
    # If it is just adding teams, we don't need to wipe everything.
    old_names_set = set(state.all_teams)
    new_names_set = set(names)
    
    is_superset = old_names_set.issubset(new_names_set)
    # Also check if order of existing teams is preserved
    preserved_order = True
    for i, old_name in enumerate(state.all_teams):
        if i >= len(names) or names[i] != old_name:
            preserved_order = False
            break

    if is_superset and preserved_order:
        # Just adding teams at the end
        state.all_teams = names
    else:
        # Destructive change (removal or reorder)
        state.all_teams = names
        # Keep locked meets, but remove unlocked ones as they may no longer be valid
        state.meets = [m for m in state.meets if m.is_locked]

    # Keep config in sync if it exists
    if state.config:
        state.config.n_teams = len(names)
    
    save_session(state)
    return {"ok": True, "team_count": len(names)}


# ── Team changes ──────────────────────────────────────────────────────────────

@app.post("/api/team-change")
async def add_team_change(req: TeamChangeRequest):
    state = get_session(req.session_id)
    if not state:
        raise HTTPException(404, "Session not found.")

    from app.models import TeamChange
    if req.action == "add":
        if req.team_name not in state.all_teams:
            state.all_teams.append(req.team_name)
    elif req.action == "remove":
        if req.team_name not in state.all_teams:
            raise HTTPException(400, f"Team '{req.team_name}' not found in roster.")

    change = TeamChange(
        action=req.action,
        team_name=req.team_name,
        effective_after_meet=req.effective_after_meet,
    )
    state.team_changes.append(change)
    save_session(state)
    return {"ok": True, "changes": [c.model_dump() for c in state.team_changes]}


@app.delete("/api/team-change/{session_id}/{index}")
async def remove_team_change(session_id: str, index: int):
    state = get_session(session_id)
    if not state:
        raise HTTPException(404, "Session not found.")
    if index < 0 or index >= len(state.team_changes):
        raise HTTPException(400, "Invalid change index.")
    state.team_changes.pop(index)
    save_session(state)
    return {"ok": True}


# ── Schedule generation ───────────────────────────────────────────────────────

@app.post("/api/generate")
async def generate(req: GenerateMeetRequest):
    state = get_session(req.session_id)
    if not state:
        raise HTTPException(404, "Session not found.")
    if not state.config:
        raise HTTPException(400, "Please complete program setup first.")
    if not state.all_teams:
        raise HTTPException(400, "Please set the team roster first.")

    # Don't allow regenerating locked meets
    locked = {m.meet_number for m in state.meets if m.is_locked}
    targets = [n for n in req.meet_numbers if n not in locked]
    if not targets:
        raise HTTPException(400, "All requested meets are locked (already played).")

    try:
        new_meets = generate_meets(state, targets)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        traceback.print_exc()
        raise HTTPException(500, "Scheduling failed. Try adjusting rooms or time slots.")

    # Merge into state
    existing = {m.meet_number: m for m in state.meets}
    for nm in new_meets:
        existing[nm.meet_number] = nm
    state.meets = [existing[k] for k in sorted(existing)]
    save_session(state)

    return {
        "ok": True,
        "meets": [m.model_dump() for m in state.meets if m.meet_number in targets],
    }


# ── Lock a meet ───────────────────────────────────────────────────────────────

@app.post("/api/lock-meet")
async def lock_meet(req: LockMeetRequest):
    state = get_session(req.session_id)
    if not state:
        raise HTTPException(404, "Session not found.")
    for m in state.meets:
        if m.meet_number == req.meet_number:
            m.is_locked = True
            save_session(state)
            return {"ok": True}
    raise HTTPException(404, f"Meet {req.meet_number} not found.")


@app.post("/api/import")
async def import_state(req: ImportRequest):
    state = get_session(req.session_id)
    if not state:
        raise HTTPException(404, "Session not found.")

    # Overwrite current state but keep the session ID consistent with the request
    imported = req.state
    imported.session_id = req.session_id
    save_session(imported)
    return {"ok": True}


# ── Legacy single-shot endpoints (kept for compatibility) ─────────────────────

@app.post("/generate-matchups/", tags=["Legacy"])
async def generate_matchups_legacy(request: MatchupsRequest) -> MatchupsResponse:
    try:
        solver = MatchupSolver(
            n_teams=request.n_teams,
            n_matches_per_team=request.n_matches_per_team,
            tournament_type=request.tournament_type,
        )
        all_possible = solver.generate_all_possible_matchups()
        solutions = solver.find_matchup_solutions(all_possible, max_solutions=request.n_matchup_solutions)
        if solutions:
            return MatchupsResponse(
                solutions={
                    f"solution_set_{i+1}": [Matchup(teams=tuple(int(x) for x in row)) for row in sol]
                    for i, sol in enumerate(solutions)
                }
            )
        return MatchupsResponse(solutions={})
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@app.post("/generate-schedule/", tags=["Legacy"])
async def generate_schedule_legacy(request: ScheduleRequest) -> ScheduleResponse:
    try:
        mu_req = MatchupsRequest(
            n_teams=request.n_teams,
            n_matches_per_team=request.n_matches_per_team,
            tournament_type=request.tournament_type,
        )
        mu_resp = await generate_matchups_legacy(mu_req)
        sched_solver = ScheduleSolver(
            n_teams=request.n_teams,
            n_matches_per_team=request.n_matches_per_team,
            n_rooms=request.n_rooms,
            tournament_type=request.tournament_type,
            phase_buffer_slots=request.phase_buffer_slots,
            international_buffer_slots=request.international_buffer_slots,
            matches_per_day=request.matches_per_day,
        )
        for proposed in mu_resp.solutions.values():
            df, relaxed = sched_solver.schedule_matches(proposed)
            if df is not None:
                items = [
                    ScheduleItem(TimeSlot=row["TimeSlot"], Room=row["Room"], Matchup=row["Matchup"])
                    for _, row in df.iterrows()
                ]
                grid = {}
                max_ts = max_rm = 0
                for _, row in df.iterrows():
                    ts, rm = int(row["TimeSlot"]), int(row["Room"])
                    max_ts = max(max_ts, ts); max_rm = max(max_rm, rm)
                    grid.setdefault(f"ts_{ts}", {})[f"room_{rm}"] = list(row["Matchup"].teams)
                return ScheduleResponse(
                    schedule=items, constraints_relaxed=relaxed,
                    grid_schedule=grid, max_sched_timeslot=sched_solver.n_time_slots,
                    max_sched_room=max_rm,
                )
        raise HTTPException(404, "No valid schedule found.")
    except ValueError as ve:
        raise HTTPException(400, str(ve))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))
