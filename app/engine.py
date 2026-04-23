"""
Core scheduling engine.

Wraps the existing MatchupSolver / ScheduleSolver with the higher-level concepts:
  - A multi-meet season with a fixed roster that can gain/lose teams between meets
  - Cross-meet pair-frequency tracking so later meets avoid repeat match-ups
  - Partial season regeneration (lock completed meets, re-solve the rest)
"""

from __future__ import annotations

import itertools
import math
import random
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

import pulp

from app.matchups import MatchupSolver
from app.models import (
    ProgramConfig,
    ProgramState,
    QuizMeetSchedule,
    ScheduleRoom,
    TeamChange,
)
from app.scheduler import ScheduleSolver


# ── Helper: build active team list for a given meet ──────────────────────────

def _active_team_ids(
    all_teams: List[str],
    changes: List[TeamChange],
    meet_number: int,
) -> List[int]:
    """
    Return the list of 1-based team IDs that are active for *meet_number*.
    Starts from the full roster and applies each change whose
    effective_after_meet < meet_number.
    """
    # Build a set of active indices (0-based into all_teams)
    active_indices: Set[int] = set(range(len(all_teams)))

    # We also need to handle teams that might have been removed from all_teams
    # but are still referenced in locked meets. However, all_teams is the current master list.
    # If a team was removed from all_teams, it's truly gone from the pool.

    for ch in sorted(changes, key=lambda c: c.effective_after_meet):
        if ch.effective_after_meet >= meet_number:
            continue
        try:
            idx = all_teams.index(ch.team_name)
            if ch.action == "remove":
                active_indices.discard(idx)
            elif ch.action == "add":
                active_indices.add(idx)
        except ValueError:
            # If team is not in all_teams, it can't be active anyway
            continue

    return sorted(i + 1 for i in active_indices)  # 1-based IDs, sorted


# ── Helper: cross-meet pair-frequency table ───────────────────────────────────

def _pair_frequencies(
    meets: List[QuizMeetSchedule],
    locked_only: bool = False,
) -> Dict[Tuple[str, str], int]:
    """
    Returns a mapping of (team_name, team_name) -> count.
    Uses names instead of IDs because IDs (indices) can change if the roster is modified.
    """
    freq: Dict[Tuple[str, str], int] = defaultdict(int)
    for m in meets:
        if locked_only and not m.is_locked:
            continue
        for room in m.rooms:
            # Use team_names for stable cross-meet history
            t1, t2, t3 = sorted(room.team_names)
            freq[(t1, t2)] += 1
            freq[(t1, t3)] += 1
            freq[(t2, t3)] += 1
    return freq


# ── Core generator ────────────────────────────────────────────────────────────

def generate_meets(
    state: ProgramState,
    meet_numbers: List[int],
) -> List[QuizMeetSchedule]:
    """
    Generate (or re-generate) the specified meet numbers.
    Locked meets are untouched; their history informs pair-frequency
    constraints for re-generated meets.
    Returns the updated list of QuizMeetSchedule objects for those meets.
    """
    cfg = state.config
    if cfg is None:
        raise ValueError("Program configuration not set.")
    if not state.all_teams:
        raise ValueError("Team roster not set.")

    # Pair frequencies from *locked* meets only
    locked_meets = [m for m in state.meets if m.is_locked]
    locked_freq = _pair_frequencies(locked_meets, locked_only=True)

    results: List[QuizMeetSchedule] = []

    # We process meets in order so earlier unlocked meets inform later ones
    running_freq = dict(locked_freq)
    sorted_targets = sorted(meet_numbers)

    for meet_num in sorted_targets:
        active_ids = _active_team_ids(state.all_teams, state.team_changes, meet_num)
        active_names = [state.all_teams[gid - 1] for gid in active_ids]
        n_active = len(active_ids)

        if n_active < 3:
            raise ValueError(
                f"Meet {meet_num} has only {n_active} active team(s). "
                "At least 3 teams are required."
            )

        mpt = cfg.matches_per_team

        # Build matchups using *local* team indices 1..n_active, then map back
        local_to_global = {i + 1: gid for i, gid in enumerate(active_ids)}
        name_to_local = {name: i + 1 for i, name in enumerate(active_names)}

        # Translate running_freq (names) into local IDs for the solver
        local_freq: Dict[Tuple[int, int], int] = defaultdict(int)
        for (n1, n2), cnt in running_freq.items():
            if n1 in name_to_local and n2 in name_to_local:
                l1, l2 = sorted([name_to_local[n1], name_to_local[n2]])
                local_freq[(l1, l2)] += cnt

        # Generate matchups
        solver_mu = MatchupSolver(
            n_teams=n_active,
            n_matches_per_team=mpt,
            tournament_type=cfg.tournament_type,
        )
        all_possible = solver_mu.generate_all_possible_matchups()

        # Filter out matchups whose pairs already exceed the cap
        # Cap = floor of average meetings across the full season
        # For simplicity, cap at 2 (same logic as original app)
        cap = 2
        filtered = [
            mu for mu in all_possible
            if all(
                local_freq.get(tuple(sorted([mu[i], mu[j]])), 0) < cap
                for i, j in itertools.combinations(range(3), 2)
            )
        ]

        # Fall back to all matchups if filtering leaves too few
        if _count_valid_matchups(filtered, n_active, mpt) < n_active * mpt // 3:
            filtered = all_possible

        # Previous repeats per team (sum of (pair frequency - 1) for all pairs met)
        team_prev_repeats = defaultdict(int)
        for (l1, l2), cnt in local_freq.items():
            reps = max(0, cnt - 1)
            team_prev_repeats[l1] += reps
            team_prev_repeats[l2] += reps

        def get_balancing_data(mu_list):
            weights = []
            mu_repeats = []
            for mu in mu_list:
                penalty = 0
                team_reps = defaultdict(int)
                for i, j in itertools.combinations(range(3), 2):
                    pair = tuple(sorted([mu[i], mu[j]]))
                    cnt = local_freq.get(pair, 0)
                    if cnt >= 1: # Already met once, this is a repeat
                        # Base penalty for the repeat itself
                        penalty += 10
                        # Extra penalty based on how many repeats the teams already have.
                        # This pushes the solver to pick teams with FEWER existing repeats.
                        penalty += team_prev_repeats[mu[i]]
                        penalty += team_prev_repeats[mu[j]]

                        team_reps[mu[i]] += 1
                        team_reps[mu[j]] += 1
                weights.append(-penalty + random.uniform(0, 0.01))
                mu_repeats.append(dict(team_reps))
            return weights, mu_repeats

        weights_f, repeats_f = get_balancing_data(filtered)
        solutions = solver_mu.find_matchup_solutions(
            filtered,
            max_solutions=1,
            matchup_weights=weights_f,
            team_prev_repeats=dict(team_prev_repeats),
            matchup_team_repeats=repeats_f,
        )
        if not solutions:
            # Relax pair cap and retry
            weights_a, repeats_a = get_balancing_data(all_possible)
            solutions = solver_mu.find_matchup_solutions(
                all_possible,
                max_solutions=1,
                matchup_weights=weights_a,
                team_prev_repeats=dict(team_prev_repeats),
                matchup_team_repeats=repeats_a,
            )
        if not solutions:
            raise ValueError(
                f"Could not find valid matchups for Meet {meet_num} "
                f"with {n_active} active teams."
            )

        chosen_matchups_local = solutions[0]  # numpy array of shape (N, 3)

        # Convert to Matchup objects using LOCAL ids (scheduler needs 1..n)
        from app.models import Matchup as MatchupModel
        matchup_objs = [MatchupModel(teams=tuple(int(x) for x in row)) for row in chosen_matchups_local]

        # Schedule
        sched_solver = ScheduleSolver(
            n_teams=n_active,
            n_matches_per_team=mpt,
            n_rooms=cfg.n_rooms,
            tournament_type=cfg.tournament_type,
            phase_buffer_slots=cfg.n_time_slots,
            international_buffer_slots=cfg.n_time_slots,
            matches_per_day=mpt,
        )
        schedule_df, relaxed = sched_solver.schedule_matches(matchup_objs)

        if schedule_df is None:
            raise ValueError(
                f"Could not build a valid schedule for Meet {meet_num}. "
                "Try adjusting rooms or time slots."
            )

        # Build ScheduleRoom objects using global team IDs and names
        rooms: List[ScheduleRoom] = []
        for _, row in schedule_df.iterrows():
            local_ids = tuple(int(x) for x in row["Matchup"].teams)
            # Handle team 0 (dummy team) by mapping to 0 and empty string
            global_ids = tuple((local_to_global[lid] if lid != 0 else 0) for lid in local_ids)
            names = tuple((state.all_teams[gid - 1] if gid != 0 else "—") for gid in global_ids)
            rooms.append(
                ScheduleRoom(
                    time_slot=int(row["TimeSlot"]),
                    room=int(row["Room"]),
                    team_ids=global_ids,
                    team_names=names,
                )
            )

        meet_sched = QuizMeetSchedule(
            meet_number=meet_num,
            active_team_ids=active_ids,
            rooms=rooms,
            constraints_relaxed=relaxed,
            is_locked=False,
        )
        results.append(meet_sched)

        # Update running frequency for subsequent meets
        for room in rooms:
            t1, t2, t3 = sorted(room.team_names)
            running_freq[(t1, t2)] = running_freq.get((t1, t2), 0) + 1
            running_freq[(t1, t3)] = running_freq.get((t1, t3), 0) + 1
            running_freq[(t2, t3)] = running_freq.get((t2, t3), 0) + 1

    return results


def _count_valid_matchups(matchups, n_teams, mpt) -> int:
    """Quick feasibility estimate."""
    team_counts = defaultdict(int)
    for mu in matchups:
        for t in mu:
            team_counts[t] += 1
    if not team_counts:
        return 0
    return min(team_counts.values())
