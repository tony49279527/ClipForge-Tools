from __future__ import annotations

import os
import subprocess
from pathlib import Path

from clipforge_v3.repositories import project_repository, shot_repository, take_repository


OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", "outputs")).resolve()


def rebuild_final_video(project_id: int) -> dict:
    shots = [dict(row) for row in shot_repository.list_shots(project_id)]
    ordered = sorted(shots, key=lambda item: item["sequence_index"])
    selected_take_ids: list[int] = []
    input_files: list[Path] = []
    missing = []
    for shot in ordered:
        if not shot.get("selected_take_id"):
            missing.append(shot["shot_id"])
            continue
        take = dict(take_repository.get_take(shot["selected_take_id"]))
        if not take.get("local_path"):
            missing.append(shot["shot_id"])
            continue
        selected_take_ids.append(take["id"])
        input_files.append(Path(take["local_path"]))
    if missing:
        raise ValueError(f"Missing selected takes for shots: {', '.join(missing)}")
    assembly_version = project_repository.get_next_final_assembly_version(project_id)
    assembly_dir = OUTPUTS_DIR / str(project_id) / "final"
    assembly_dir.mkdir(parents=True, exist_ok=True)
    concat_file = assembly_dir / f"assembly_{assembly_version}.txt"
    output_path = assembly_dir / f"final_v{assembly_version}.mp4"
    concat_file.write_text("\n".join(f"file '{path.as_posix()}'" for path in input_files), encoding="utf-8")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c",
        "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    assembly_id = project_repository.create_final_assembly(
        {
            "project_id": project_id,
            "version": assembly_version,
            "status": "built",
            "output_path": str(output_path),
            "assembly_take_ids_json": selected_take_ids,
            "invalidated": False,
        }
    )
    project_repository.update_project(project_id, {"final_assembly_valid": 1})
    return {"id": assembly_id, "output_path": str(output_path), "assembly_take_ids": selected_take_ids}

