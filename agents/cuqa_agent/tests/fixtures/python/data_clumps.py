"""Python fixture: DataClumps smell — same 3+ params in multiple functions."""


def create_user(username, email, password, age):
    return {"username": username, "email": email, "password": password, "age": age}


def update_user(username, email, password, role):
    # username, email, password appear together again → DataClumps
    return {"username": username, "email": email, "password": password, "role": role}
