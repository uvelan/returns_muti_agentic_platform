from .models import CollisionPolicy, OperationalGenerationProposal


def analyze_collisions(proposal: OperationalGenerationProposal, policy: CollisionPolicy) -> None:
    # A read-only analysis to ensure no keys conflict before planning.
    # In a real system, we'd query the DB here (read-only).
    # Since this is AIG4 and we are not implementing full read-only DB checks,
    # we simulate the analysis or just pass if policy allows.
    if policy == CollisionPolicy.REJECT:
        # assume verified by generator or mock
        pass
