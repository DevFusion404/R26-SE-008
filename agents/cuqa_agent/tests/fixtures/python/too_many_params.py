"""Python fixture: TooManyParameters — 6 real parameters (threshold > 5)."""


def needs_many_args(alpha, beta, gamma, delta, epsilon, zeta):
    """6 real parameters — triggers TooManyParameters (> 5)."""
    return alpha + beta + gamma + delta + epsilon + zeta


class MyService:
    def method_with_self_plus_six(self, a, b, c, d, e, f):
        """self + 6 real params — self is excluded, 6 real → triggers."""
        return a + b + c + d + e + f

    def method_with_five(self, a, b, c, d, e):
        """self + 5 real params — should NOT trigger (exactly 5, not > 5)."""
        return a + b + c + d + e
