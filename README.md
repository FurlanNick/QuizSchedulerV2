# QuizMeet Scheduler v2

Multi-meet quiz scheduling with roster management, team changes between meets, and cross-reference tracking.

## Features

- **Sign-in by name** — sessions persist in memory; returning users resume their season
- **Season setup** — configure meets, rooms, time slots, matches per team, and tournament type
- **Team roster** — enter team names once; they replace numeric IDs throughout the app
- **Roster changes** — add or remove teams between specific meets; the scheduler automatically accounts for the correct active roster per meet
- **Per-meet generation** — generate or regenerate any individual meet; locked meets are preserved and their matchup history informs future meets
- **Position constraints** — each team fills positions A, B, and C evenly across rounds
- **Cross-reference tab** — view head-to-head counts across any subset of completed meets
- **Legacy API** — original `/generate-matchups/` and `/generate-schedule/` endpoints are still available

## Quick Start (Docker)

```bash
docker-compose up --build
```

Then open http://localhost:8888

## Quick Start (local)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Workflow

1. **Sign in** with your name or program name
2. **Setup** — set number of meets, rooms, time slots, and matches per team
3. **Teams** — paste team names (one per line) and save
4. **Teams → Changes** — record any team additions/removals and which meet they take effect after
5. **Schedule** — generate each meet individually; view the grid, then lock it once it's been played
6. **Cross-Ref** — inspect head-to-head counts to verify fairness

## Regenerating after roster changes

If teams are added or removed after Meet 1:
- Lock Meet 1 (it's been played and shouldn't change)
- Record the change on the Teams tab (e.g. "remove Team X after Meet 1")
- Regenerate Meet 2 (and any subsequent meets)
- The scheduler will use only the active roster for each meet and respect pair-frequency history from locked meets

## Notes on scheduling constraints

- No team plays in the same time slot twice per meet
- Each team fills positions A, B, C exactly once per three matches
- Pair frequencies from previous meets are used to minimize repeat matchups
- If the solver can't find a perfect solution, it relaxes room-diversity or consecutive-match constraints and notifies you
