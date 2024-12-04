from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING

from randovania.exporter.game_exporter import GameExporter, GameExportParams

if TYPE_CHECKING:
    from randovania.lib import status_update_lib


# TODO
@dataclasses.dataclass(frozen=True)
class FusionGameExportParams(GameExportParams):
    input_path: Path
    output_path: Path


class FusionGameExporter(GameExporter):
    _busy: bool = False

    @property
    def can_start_new_export(self) -> bool:
        """
        Checks if the exporter is busy right now
        """
        return self._busy

    @property
    def export_can_be_aborted(self) -> bool:
        """
        Checks if export_game can be aborted
        """
        return False

    def export_params_type(self) -> type[GameExportParams]:
        """
        Returns the type of the GameExportParams expected by this exporter.
        """
        return FusionGameExportParams

    def _do_export_game(
        self,
        patch_data: dict,
        export_params: GameExportParams,
        progress_update: status_update_lib.ProgressUpdateCallable,
    ) -> None:
        assert isinstance(export_params, FusionGameExportParams)
        from mars_patcher.patcher import patch

        json_path = Path(export_params.output_path.parent).joinpath(f"{export_params.output_path.stem}.json")

        with json_path.open("w+") as f:
            json.dump(patch_data, f, indent=4)

        def hacked_progress(a: float, b: str) -> None:
            progress_update(b, a)

        patch(str(export_params.input_path), str(export_params.output_path), str(json_path), hacked_progress)
        # raise RuntimeError("Needs to be implemented")
