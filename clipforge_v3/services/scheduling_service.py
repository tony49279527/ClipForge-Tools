from __future__ import annotations

from collections import defaultdict


def build_dependency_graph(shots: list[dict]) -> dict[str, set[str]]:
    ordered = sorted(shots, key=lambda item: item["sequence_index"])
    graph: dict[str, set[str]] = {shot["shot_id"]: set() for shot in ordered}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for shot in ordered:
        grouped[(shot.get("continuity_group") or "default").strip()].append(shot)
        if shot.get("depends_on_shot_id"):
            graph[shot["shot_id"]].add(shot["depends_on_shot_id"])
    for _, group_shots in grouped.items():
        if len(group_shots) < 2:
            continue
        for prev, current in zip(group_shots, group_shots[1:]):
            graph[current["shot_id"]].add(prev["shot_id"])
    return graph


def detect_cycle(graph: dict[str, set[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str]:
        if node in visiting:
            idx = path.index(node)
            return path[idx:] + [node]
        if node in visited:
            return []
        visiting.add(node)
        path.append(node)
        for dependency in graph.get(node, set()):
            cycle = visit(dependency)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return []


def compute_schedule_state(shots: list[dict], selected_business_shot_ids: set[str], failed_business_shot_ids: set[str]) -> list[dict]:
    graph = build_dependency_graph(shots)
    cycle = detect_cycle(graph)
    states = []
    for shot in sorted(shots, key=lambda item: item["sequence_index"]):
        deps = sorted(graph.get(shot["shot_id"], set()))
        waiting_on = [dep for dep in deps if dep not in selected_business_shot_ids]
        blocked_by_failure = [dep for dep in deps if dep in failed_business_shot_ids]
        if cycle:
            status = "blocked_cycle"
        elif blocked_by_failure:
            status = "blocked_failed_dependency"
        elif waiting_on:
            status = "waiting_dependency"
        else:
            status = "ready_parallel" if not deps else "ready_sequential"
        states.append(
            {
                "shot_id": shot["shot_id"],
                "dependencies": deps,
                "waiting_on": waiting_on,
                "blocked_by_failure": blocked_by_failure,
                "status": status,
                "cycle": cycle,
            }
        )
    return states

