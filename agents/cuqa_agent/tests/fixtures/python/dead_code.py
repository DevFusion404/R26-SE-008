"""Python fixture: DeadCode — a function defined but never called/referenced."""


def _helper_unused():
    """This function is never referenced anywhere → DeadCode."""
    return 42


def used_function():
    """This one is at least defined; the name appears below."""
    return 1


result = used_function()
