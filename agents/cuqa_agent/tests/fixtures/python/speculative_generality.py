"""Python fixture: SpeculativeGenerality — inherits from ABC/Base/Mixin."""

from abc import ABC, abstractmethod


class AbstractProcessor(ABC):
    """Inherits ABC → SpeculativeGenerality suspected."""

    @abstractmethod
    def process(self, data):
        pass


class BaseMixin:
    """Inherits Base-prefixed class."""
    pass


class LoggerMixin:
    """Mixin class → SpeculativeGenerality."""
    pass
