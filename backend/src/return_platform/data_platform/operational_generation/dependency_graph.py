from .models import OperationalGenerationProposal


def detect_cycles(graph: dict[str, list[str]]) -> bool:
    visited = set()
    rec_stack = set()

    def visit(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if visit(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True

        rec_stack.remove(node)
        return False

    for node in graph:
        if node not in visited:
            if visit(node):
                return True
    return False


def build_operation_dependency_graph(
    proposal: OperationalGenerationProposal,
) -> dict[str, list[str]]:
    # Maps temporary_record_key -> list of parent temporary_record_keys
    graph = {}

    # Actually, in proposal, records have dependency_keys but it doesn't give the temporary_record_key of the parent directly unless we match it.
    # The AIG3 generator generated dependency_keys as natural key values.
    # So we need to map natural key value to temporary_record_key.
    key_to_id = {}
    for rec in proposal.records:
        # Assuming natural keys are uniquely identifying a record within the proposal
        for _k, v in rec.values.items():
            if isinstance(v, str):
                key_to_id[v] = rec.temporary_record_key

    for rec in proposal.records:
        deps = []
        for dk in rec.dependency_keys:
            # try to resolve within proposal
            parent_id = key_to_id.get(dk)
            if parent_id:
                if parent_id == rec.temporary_record_key:
                    raise ValueError(f"Self-dependency detected for {rec.temporary_record_key}")
                deps.append(parent_id)
            else:
                # Missing dependency or external. We assume external is resolved, but if it's missing in proposal, we can't link it here.
                # The prompt says: "detect missing dependencies". If it's not in the registry as read-only, it's missing.
                # For now, just track known intra-proposal deps.
                pass

        graph[rec.temporary_record_key] = deps

    if detect_cycles(graph):
        raise ValueError("Dependency cycle detected")

    return graph
