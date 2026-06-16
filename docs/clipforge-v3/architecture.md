# ClipForge 3.0 Architecture

ClipForge 3.0 is isolated under `clipforge_v3/` and mounted under `/v3`.

The first-stage runtime path is:

1. `router.py` accepts project creation requests.
2. `project_service.py` creates a `v3_projects` record.
3. `product_truth_service.py` derives an initial Product Truth record.
4. `shot_service.py` generates shot contracts and first prompt versions.
5. `continuity_service.py` seeds continuity state rows.

The legacy 1.0 and 2.0 code paths remain in `app.py`, `db.py`, `video_core.py`, and `task_queue.py`. No existing routes are renamed or removed.

The 3.0 package avoids queue-to-`app.py` reverse imports. Any future RQ jobs should call `clipforge_v3.tasks`.

Director decision flow now implemented:

1. `ProductTruthInput` is normalized and converted into strict JSON.
2. The JSON is validated by Pydantic after parse, with one repair retry.
3. Product Truth must be manually confirmed before director planning.
4. Uploaded reference images are audited and stored with role metadata.
5. Director planning builds:
   - Reference Role Map
   - Seedance Mode Decision
   - Fidelity Allocation
   - Shot Contracts
