"""
/refactoriq command parsing
===========================
R26-SE-008 | Bandara S M Y M | IT22277886

Pure parsing of PR comments into commands — no I/O, so the webhook can parse
cheaply and reject non-commands before anything is queued.

    /refactoriq <command> [arguments...]

Only a line that STARTS with /refactoriq counts (leading spaces allowed), and
only the first such line: one command per comment, so a comment cannot smuggle
a second, unintended action. Text inside quotes ("> /refactoriq ...") or code
is not a command because it does not start the line.
"""

import re

PREFIX = "/refactoriq"
MAX_BODY = 10_000
MAX_ARGS = 200

#: Command -> whether it changes workflow state (needs write access + a
#: current commit). Read-only commands only describe what the PR already shows.
COMMANDS = {
    "help": False,
    "list": False,
    "show-selection": False,
    "select": True,
    "select-group": True,
    "deselect": True,
    "clear-selection": True,
    "generate-plan": True,
    "show-plan": False,
    "approve-plan": True,
    "reject-plan": True,
    "submit-plan": True,
    "show-transformations": False,
    "accept-transformation": True,
    "reject-transformation": True,
    "accept-all-validated": True,
    "submit-refactoring": True,
    "cancel": True,
}

#: Check Run button identifiers (max 20 chars) -> the command they stand for.
REQUESTED_ACTIONS = {
    "accept_validated": ("accept-all-validated", []),
    "reject_all": ("cancel", []),
    "apply_accepted": ("submit-refactoring", ["confirm"]),
}

_LINE_RE = re.compile(rf"^\s*{re.escape(PREFIX)}(?:\s+(.*))?$", re.IGNORECASE)


def parse_command(body) -> dict | None:
    """{name, args, raw, known, mutating} for the first command line, or None."""
    if not isinstance(body, str) or not body.strip():
        return None
    for line in body[:MAX_BODY].splitlines():
        match = _LINE_RE.match(line)
        if not match:
            continue
        tokens = [t for t in re.split(r"[\s,]+", (match.group(1) or "").strip()) if t]
        name = tokens[0].lower() if tokens else "help"
        return {
            "name": name,
            "args": tokens[1:MAX_ARGS + 1],
            "raw": line.strip()[:500],
            "known": name in COMMANDS,
            "mutating": COMMANDS.get(name, False),
        }
    return None


def command_for_action(identifier: str) -> dict | None:
    if identifier not in REQUESTED_ACTIONS:
        return None
    name, args = REQUESTED_ACTIONS[identifier]
    return {"name": name, "args": list(args), "raw": f"[button] {identifier}",
            "known": True, "mutating": COMMANDS[name]}
