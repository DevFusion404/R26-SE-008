"""Python fixture: BareExcept smell."""


def risky():
    try:
        x = int("hello")
    except:          # BareExcept — catches everything including SystemExit
        x = 0
    return x


def safer():
    try:
        x = int("hello")
    except ValueError:  # NOT a bare except — specific exception type
        x = 0
    return x
