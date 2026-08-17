"""Python fixture: MessageChains — deep attribute/call chain (depth >= 3)."""


def get_city(user):
    # Chain depth 3: user.address.city.name
    return user.address.city.name
