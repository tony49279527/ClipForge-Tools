from __future__ import annotations

from clipforge_v3.services import project_service
from clipforge_v3.services.generation_service import process_generation_submission


def bootstrap_project_task(project_id: int) -> dict:
    status = project_service.get_project_status(project_id)
    return status.model_dump()


def run_generation_submission_task(submission_id: int) -> dict:
    return process_generation_submission(submission_id)
