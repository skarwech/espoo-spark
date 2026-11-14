"""Deterministic greedy and exact small-instance solvers for event b-matching."""
from __future__ import annotations

import random
from collections import Counter, defaultdict
from itertools import combinations

TIER_POINTS = {"strong": 3, "worth": 2, "stretch": 1}


def greedy_assign(edge_tiers: dict[tuple[str, str], str], capacities: dict[str, int], seed: str) -> list[dict]:
    """Greedy production assignment: reserve Stretch capacity, then fill each attendee's first and second slot."""
    rng = random.Random(seed)
    people = sorted({candidate for candidate, _company in edge_tiers})
    companies = sorted(capacities)
    candidate_order = people[:]
    rng.shuffle(candidate_order)
    occupied_by_person = defaultdict(set)
    chosen_by_person = defaultdict(set)
    per_company = defaultdict(list)
    next_index = defaultdict(int)

    def add(candidate_id: str, company_id: str) -> bool:
        cap = capacities[company_id]
        if len(chosen_by_person[candidate_id]) >= 2 or company_id in chosen_by_person[candidate_id] or next_index[company_id] >= cap:
            return False
        index = next_index[company_id]
        while index < cap and index in occupied_by_person[candidate_id]:
            index += 1
        if index >= cap:
            return False
        next_index[company_id] = index + 1
        occupied_by_person[candidate_id].add(index)
        chosen_by_person[candidate_id].add(company_id)
        per_company[company_id].append({"candidate_id": candidate_id, "company_id": company_id,
                                        "index": index, "tier": edge_tiers[(candidate_id, company_id)]})
        return True

    for company_id in companies:
        cap = capacities[company_id]
        stretch = [candidate for candidate in candidate_order
                   if edge_tiers.get((candidate, company_id)) == "stretch"]
        rng.shuffle(stretch)
        reserve = min(round(cap * 0.20), len(stretch))
        for candidate in stretch:
            if len(per_company[company_id]) >= reserve:
                break
            add(candidate, company_id)

    rank = {"strong": 0, "worth": 1, "stretch": 2}
    for round_index in range(2):
        for candidate in candidate_order:
            if len(chosen_by_person[candidate]) > round_index:
                continue
            options = [(company, tier) for (person, company), tier in edge_tiers.items()
                       if person == candidate and company not in chosen_by_person[candidate]]
            options.sort(key=lambda item: (rank[item[1]], item[0]))
            if not options:
                continue
            best_rank = rank[options[0][1]]
            ties = [item for item in options if rank[item[1]] == best_rank]
            rng.shuffle(ties)
            for company_id, _tier in ties + [item for item in options if item not in ties]:
                if add(candidate, company_id):
                    break

    return sorted((item for rows in per_company.values() for item in rows),
                  key=lambda item: (item["company_id"], item["index"], item["candidate_id"]))


def objective_weights(people_count: int) -> tuple[int, int, int]:
    """Return coefficients that make slots, two-slot coverage, breadth, then tier points lexicographic."""
    max_slots = 2 * people_count
    max_tier_points = 3 * max_slots
    breadth_weight = max_tier_points + 1
    two_slot_weight = people_count * breadth_weight + max_tier_points + 1
    slot_weight = people_count * two_slot_weight + people_count * breadth_weight + max_tier_points + 1
    return slot_weight, two_slot_weight, breadth_weight


def objective_score(assignments: list[dict], people_count: int | None = None) -> int:
    """Lexicographic objective: fill slots, two-slot attendees, attendee breadth, then evidence tier."""
    per_person = Counter(item["candidate_id"] for item in assignments)
    two_slot_people = sum(count >= 2 for count in per_person.values())
    covered_people = len(per_person)
    tier_points = sum(TIER_POINTS[item["tier"]] for item in assignments)
    count = people_count if people_count is not None else len(per_person)
    slot_weight, two_slot_weight, breadth_weight = objective_weights(count)
    return slot_weight * len(assignments) + two_slot_weight * two_slot_people + breadth_weight * covered_people + tier_points


def exact_assign(edge_tiers: dict[tuple[str, str], str], capacities: dict[str, int]) -> list[dict]:
    """Solve the same assignment integer program exactly with a company-time occupancy-mask DP.

    Every candidate chooses zero, one or two different companies at distinct times. Each company-time cell can
    contain at most one candidate, and the number of available cells is its capacity.
    """
    companies = sorted(capacities)
    people = sorted({candidate for candidate, _company in edge_tiers})
    slot_weight, two_slot_weight, breadth_weight = objective_weights(len(people))
    time_count = max(capacities.values(), default=0)
    company_index = {company: index for index, company in enumerate(companies)}
    options_by_person = {}

    for candidate in people:
        edges = [(company, tier) for (person, company), tier in edge_tiers.items() if person == candidate]
        options = [()]
        for company, tier in edges:
            for slot_index in range(capacities[company]):
                options.append(((company, slot_index, tier),))
        for (company_a, tier_a), (company_b, tier_b) in combinations(edges, 2):
            if company_a == company_b:
                continue
            for slot_a in range(capacities[company_a]):
                for slot_b in range(capacities[company_b]):
                    if slot_a != slot_b:
                        options.append(((company_a, slot_a, tier_a), (company_b, slot_b, tier_b)))
        options_by_person[candidate] = options

    # state: occupied company-time bitmask -> (best objective, selected assignments)
    states = {0: (0, ())}
    for candidate in people:
        next_states = {}
        for occupied, (score, selected) in states.items():
            for option in options_by_person[candidate]:
                option_mask = 0
                rows = []
                tier_points = 0
                for company, slot_index, tier in option:
                    bit = 1 << (company_index[company] * time_count + slot_index)
                    option_mask |= bit
                    rows.append((candidate, company, slot_index, tier))
                    tier_points += TIER_POINTS[tier]
                if occupied & option_mask:
                    continue
                option_score = (slot_weight * len(option) + (two_slot_weight if len(option) == 2 else 0)
                                + (breadth_weight if option else 0) + tier_points)
                key = occupied | option_mask
                proposed = (score + option_score, selected + tuple(rows))
                current = next_states.get(key)
                if current is None or proposed[0] > current[0] or (proposed[0] == current[0] and proposed[1] < current[1]):
                    next_states[key] = proposed
        states = next_states

    _score, selected = max(states.values(), key=lambda item: (item[0], tuple(reversed(item[1]))))
    return [{"candidate_id": candidate, "company_id": company, "index": slot_index, "tier": tier}
            for candidate, company, slot_index, tier in selected]


def benchmark(seed: int = 20261115, instances: int = 10) -> dict:
    """Compare production greedy against exact solutions on ten seeded, deliberately small feasibility graphs."""
    rng = random.Random(seed)
    ratios, greedy_scores, optimum_scores = [], [], []
    for instance in range(instances):
        candidates = [f"P{index:02d}" for index in range(6)]
        companies = [f"C{index:02d}" for index in range(4)]
        capacities = {company: rng.choice((2, 3)) for company in companies}
        edge_tiers = {}
        for candidate in candidates:
            for company in companies:
                if rng.random() < 0.78:
                    edge_tiers[(candidate, company)] = rng.choices(
                        ("strong", "worth", "stretch"), weights=(4, 4, 2), k=1)[0]
        for candidate in candidates:
            if not any(person == candidate for person, _company in edge_tiers):
                company = rng.choice(companies)
                edge_tiers[(candidate, company)] = rng.choice(tuple(TIER_POINTS))
        greedy = greedy_assign(edge_tiers, capacities, f"{seed}:{instance}")
        exact = exact_assign(edge_tiers, capacities)
        people_count = len({candidate for candidate, _company in edge_tiers})
        greedy_score = objective_score(greedy, people_count)
        optimum_score = objective_score(exact, people_count)
        ratios.append(greedy_score / max(1, optimum_score))
        greedy_scores.append(greedy_score)
        optimum_scores.append(optimum_score)
    aggregate = sum(greedy_scores) / max(1, sum(optimum_scores))
    return {"instances": instances, "seed": seed, "aggregate_ratio": round(aggregate, 6),
            "minimum_instance_ratio": round(min(ratios, default=0), 6),
            "greedy_scores": greedy_scores, "exact_scores": optimum_scores,
            "objective": "Lexicographic: scheduled slots, people with two slots, people with any slot, then tier points (strong=3, worth=2, stretch=1)."}
