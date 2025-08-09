from __future__ import annotations

from typing import TYPE_CHECKING

from randovania.game_description.db.dock_node import DockNode
from randovania.games.am2r.layout.am2r_configuration import AM2RConfiguration
from randovania.generator.base_patches_factory import BasePatchesFactory, weaknesses_for_unlocked_saves
from randovania.generator.teleporter_distributor import (
    TeleporterHelper,
    get_dock_connections_assignment_for_teleporter,
    get_teleporter_connections,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from random import Random

    from randovania.game_description.db.area import Area
    from randovania.game_description.db.dock import DockWeakness
    from randovania.game_description.db.node import Node
    from randovania.game_description.game_database_view import GameDatabaseView
    from randovania.game_description.game_patches import GamePatches


class AM2RBasePatchesFactory(BasePatchesFactory[AM2RConfiguration]):
    def assign_static_dock_weakness(
        self, configuration: AM2RConfiguration, game: GameDatabaseView, initial_patches: GamePatches
    ) -> GamePatches:
        parent = super().assign_static_dock_weakness(configuration, game, initial_patches)

        dock_weakness: list[tuple[DockNode, DockWeakness]] = []

        door_type = game.find_dock_type_by_short_name("door")

        blue_door = game.get_dock_weakness("door", "Normal Door (Forced)")
        open_transition_door = game.get_dock_weakness("door", "Open Transition")
        are_transitions_shuffled = (
            open_transition_door in configuration.dock_rando.types_state[door_type].can_change_from
        )

        # TODO: separate these two into functions, so that they can be tested more easily?
        if configuration.blue_save_doors or configuration.force_blue_labs:

            def area_filter(area: Area) -> bool:
                return (configuration.blue_save_doors and area.extra.get("unlocked_save_station") is True) or (
                    configuration.force_blue_labs and area.extra.get("force_blue_labs") is True
                )

            def dock_filter(node: DockNode) -> bool:
                return node.default_dock_weakness != open_transition_door or (
                    node.default_dock_weakness == open_transition_door and are_transitions_shuffled
                )

            dock_weakness.extend(
                weaknesses_for_unlocked_saves(
                    game,
                    unlocked_weakness=blue_door,
                    target_dock_type=door_type,
                    area_filter=area_filter,
                    dock_filter=dock_filter,
                )
            )

        return parent.assign_dock_weakness(dock_weakness)

    def dock_connections_assignment(
        self, configuration: AM2RConfiguration, game: GameDatabaseView, rng: Random
    ) -> Iterable[tuple[DockNode, Node]]:
        yield from super().dock_connections_assignment(configuration, game, rng)
        dock_assignment = []

        pipes_connection = get_teleporter_connections(configuration.teleporters, game, rng)
        pipes_assignment = get_dock_connections_assignment_for_teleporter(
            configuration.teleporters, game, pipes_connection
        )

        all_helpers = [
            TeleporterHelper(node.identifier, node.default_connection)
            for region, area, node in game.iterate_nodes_of_type(DockNode)
            if node.extra.get("is_area_transition")
        ]
        # all_helpers_tuple = tuple(helper for helper in all_helpers if helper.teleporter in configuration.teleporters.editable_teleporters)
        all_helpers_tuple = tuple(helper for helper in all_helpers)
        region_connections = get_teleporter_connections(configuration.teleporters, game, rng, all_helpers_tuple, True)

        region_assignment = get_dock_connections_assignment_for_teleporter(
            configuration.teleporters, game, region_connections
        )

        print("region assignment:")
        for source, dest in region_assignment:
            print(f"{source.identifier}  -> {dest.identifier}")
        print("------")

        dock_assignment = region_assignment + pipes_assignment

        yield from dock_assignment
