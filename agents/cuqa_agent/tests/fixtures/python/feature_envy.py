"""Python fixture: FeatureEnvy smell.

The rule: ext_acc > self_acc AND ext_acc >= 3.
"""


class Processor:
    def __init__(self):
        self.name = "proc"

    def process(self, other):
        # Accesses other's attributes more than self → FeatureEnvy
        val = other.data
        val += other.value
        val += other.score
        # Only 0 self accesses; 3 external → ext_acc > self_acc AND ext_acc >= 3
        return val
