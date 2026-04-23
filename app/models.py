from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Tuple, Union


# ── Auth / Session ────────────────────────────────────────────────────────────

class SignInRequest(BaseModel):
    name: str

class SignInResponse(BaseModel):
    session_id: str
    name: str
    is_new: bool


# ── Program Setup ─────────────────────────────────────────────────────────────

class ProgramConfig(BaseModel):
    n_quiz_meets: int = Field(..., ge=1, le=100)
    n_rooms: int = Field(..., ge=1, le=100)
    n_time_slots: int = Field(..., ge=1, le=100)
    n_teams: int = Field(..., ge=3, le=500)
    matches_per_team: int = Field(3, ge=1, le=100)
    tournament_type: str = Field("international")
    matches_per_day: int = Field(3, ge=1, le=100)


# ── Teams ─────────────────────────────────────────────────────────────────────

class TeamRoster(BaseModel):
    teams: List[str]  # ordered list of team names; index+1 = team_id


class TeamChange(BaseModel):
    """A team added or removed between meets."""
    action: str          # "add" | "remove"
    team_name: str
    effective_after_meet: int  # changes apply starting from meet N+1


# ── Matchup / Schedule primitives ────────────────────────────────────────────

class Matchup(BaseModel):
    teams: Tuple[int, int, int]


class ScheduleRoom(BaseModel):
    time_slot: int
    room: int
    team_ids: Tuple[int, int, int]
    team_names: Tuple[str, str, str]


class QuizMeetSchedule(BaseModel):
    meet_number: int
    active_team_ids: List[int]   # team ids competing in this meet
    rooms: List[ScheduleRoom]
    constraints_relaxed: List[str] = []
    is_locked: bool = False      # True once the meet has been played


# ── Full program state (persisted per session) ────────────────────────────────

class ProgramState(BaseModel):
    session_id: str
    owner_name: str
    config: Optional[ProgramConfig] = None
    all_teams: List[str] = []          # master list; position = id-1
    team_changes: List[TeamChange] = []
    meets: List[QuizMeetSchedule] = []


# ── Request / Response models for API ────────────────────────────────────────

class SetupRequest(BaseModel):
    session_id: str
    config: ProgramConfig

class RosterRequest(BaseModel):
    session_id: str
    teams: List[str]

class TeamChangeRequest(BaseModel):
    session_id: str
    action: str          # "add" | "remove"
    team_name: str
    effective_after_meet: int

class GenerateMeetRequest(BaseModel):
    session_id: str
    meet_numbers: List[int]   # which meet(s) to (re)generate

class LockMeetRequest(BaseModel):
    session_id: str
    meet_number: int

class ImportRequest(BaseModel):
    session_id: str
    state: ProgramState

class MatchupsRequest(BaseModel):
    n_teams: int
    n_matches_per_team: int
    n_matchup_solutions: int = 1
    tournament_type: str = "international"

class Matchup(BaseModel):
    teams: Tuple[int, int, int]

class MatchupsResponse(BaseModel):
    solutions: Dict[str, List[Matchup]]

class ScheduleRequest(BaseModel):
    n_teams: int
    n_matches_per_team: int
    n_rooms: int
    tournament_type: str = "international"
    phase_buffer_slots: int = 2
    international_buffer_slots: int = 5
    matches_per_day: int = 3

class ScheduleItem(BaseModel):
    TimeSlot: int
    Room: int
    Matchup: Matchup

class ScheduleResponse(BaseModel):
    schedule: List[ScheduleItem]
    constraints_relaxed: List[str]
    grid_schedule: Dict[str, Dict[str, List[Union[int, str]]]] = Field(default_factory=dict)
    max_sched_timeslot: int = 0
    max_sched_room: int = 0
