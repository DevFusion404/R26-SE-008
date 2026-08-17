"""Python fixture: PrimitiveObsession — 4+ primitive-annotated fields."""


class UserRecord:
    name: str
    age: int
    score: float
    is_active: bool
    # 4 primitive-type annotations → PrimitiveObsession triggered (>= 4)
