"""Business services for the DIWO orchestration backend.

    workflow_service        coordinates the whole workflow and its decisions
    planning_service        filtered CUQA report -> RDP -> normalized plan
    transformation_service  transformation results, validation, metrics
    archive_service         the whole-project ZIP
    git_service             clone, branch, apply, commit, push

Routes call into here; nothing here imports Flask request state.
"""
