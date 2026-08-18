"""Python fixture: InappropriateIntimacy — accesses external object's private attr."""


class Formatter:
    pass


class Renderer:
    def render(self, fmt: Formatter):
        # Accesses fmt._internal (private attr of an external object)
        return fmt._internal
# _internal starts with "_" and fmt is not self/cls → InappropriateIntimacy
