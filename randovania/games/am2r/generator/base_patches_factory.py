from __future__ import annotations

import dataclasses
from collections import namedtuple
from enum import Enum
from typing import TYPE_CHECKING

from randovania.game_description.db.area_identifier import AreaIdentifier
from randovania.games.am2r.layout.am2r_configuration import AM2RConfiguration
from randovania.generator.base_patches_factory import BasePatchesFactory, weaknesses_for_unlocked_saves
from randovania.generator.teleporter_distributor import (
    get_dock_connections_assignment_for_teleporter,
    get_teleporter_connections,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from random import Random

    from randovania.game_description.db.area import Area
    from randovania.game_description.db.dock import DockWeakness
    from randovania.game_description.db.dock_node import DockNode
    from randovania.game_description.db.node import Node
    from randovania.game_description.game_description import GameDescription
    from randovania.game_description.game_patches import GamePatches


Point = namedtuple("Point", ["x", "y"])


class Direction(Enum):
    UP = 0
    RIGHT = 1
    DOWN = 2
    LEFT = 3


class MapValue(Enum):
    OPEN = "0"
    WALL = "1"
    DOOR = "2"


class AM2RBasePatchesFactory(BasePatchesFactory[AM2RConfiguration]):
    def assign_static_dock_weakness(
        self, configuration: AM2RConfiguration, game: GameDescription, initial_patches: GamePatches
    ) -> GamePatches:
        parent = super().assign_static_dock_weakness(configuration, game, initial_patches)

        dock_weakness: list[tuple[DockNode, DockWeakness]] = []

        door_type = game.dock_weakness_database.find_type("door")

        blue_door = game.dock_weakness_database.get_by_weakness("door", "Normal Door (Forced)")
        open_transition_door = game.dock_weakness_database.get_by_weakness("door", "Open Transition")
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
        self, configuration: AM2RConfiguration, game: GameDescription, rng: Random
    ) -> Iterable[tuple[DockNode, Node]]:
        teleporter_connection = get_teleporter_connections(configuration.teleporters, game, rng)
        dock_assignment = get_dock_connections_assignment_for_teleporter(
            configuration.teleporters, game, teleporter_connection
        )
        if False:
            from randovania.layout.permalink import Permalink

            a = Permalink.from_str("DQxqTBP7Gi5E_I866nZo9PqE0dzACEIS2Orx_IgZ83f4AL8h")

            # base tourno preset
            last = Permalink.from_str("De-XZIfmNGpME_sXkhOgiMk8K4Bv2MV0JViB9mDOvbHu1bkAAJjb")
            last = dataclasses.replace(
                last,
                parameters=dataclasses.replace(
                    last.parameters, seed_number=last.parameters.seed_number + 67_730 + 201_100 + 202_500
                ),
            )
            last_as_str = last.as_base64_str

            # base tourno + NO PMT progression
            last = Permalink.from_str("Df5qEBTcU2pME_saoChReRSWlPj2bmoppvy2YqTcYNlWVFejmYwAAA9V")
            last = dataclasses.replace(
                last, parameters=dataclasses.replace(last.parameters, seed_number=last.parameters.seed_number + 55_000)
            )
            last_as_str = last.as_base64_str

            def create_mapper_dict():
                final_dict = []
                count = 0
                for point, tile in minimap_grid.items():
                    if tile is None:
                        continue
                    count += 1
                    try:
                        special = int(tile[0][5])
                    except:
                        special = 0

                    try:
                        corner = int(tile[0][6])
                    except:
                        corner = 0

                    final_dict.append(
                        {
                            "color": int(tile[0][4]),
                            "corner": corner,
                            "isCorner": corner != 0,
                            "special": special,
                            "wallD": int(tile[0][Direction.DOWN.value]),
                            "wallL": int(tile[0][Direction.LEFT.value]),
                            "wallU": int(tile[0][Direction.UP.value]),
                            "wallR": int(tile[0][Direction.RIGHT.value]),
                            "x": point.x,
                            "y": point.y,
                        }
                    )
                import json

                print(json.dumps(final_dict))
                print(count)

            def get_relative_offsets(room: Area):
                data = room.extra["minimap_data"]
                xs = [value for entry in data for key, value in entry.items() if key == "x"]
                if len(xs) <= 0:
                    raise ValueError(room.name)
                min_x = min(xs)
                ys = [value for entry in data for key, value in entry.items() if key == "y"]
                if len(ys) <= 0:
                    raise ValueError(room.name)
                min_y = min(ys)
                return min_x, min_y

            def get_possible_rooms(rooms: list[Area], direction: Direction, current_position: Point):
                valid_rooms = []
                possible_rooms_and_positions = []
                for room in rooms:
                    relative_x, relative_y = get_relative_offsets(room)
                    for tile in room.extra["minimap_data"]:
                        tile_data = tile["tile_data"]
                        # needs to have at least one tile that points in our direction
                        if tile_data[direction.value] == MapValue.DOOR.value:
                            possible_rooms_and_positions.append(
                                (
                                    room,
                                    Point(
                                        current_position.x - (tile["x"] - relative_x),
                                        current_position.y - (tile["y"] - relative_y),
                                    ),
                                )
                            )

                for room, potential_position in possible_rooms_and_positions:
                    relative_x, relative_y = get_relative_offsets(room)
                    valid = True
                    for tile in room.extra["minimap_data"]:
                        tile_data = tile["tile_data"]

                        position = Point(
                            potential_position.x + (tile["x"] - relative_x),
                            potential_position.y + (tile["y"] - relative_y),
                        )

                        # invalid if it goes out of map bounds
                        if position.x < 0 or position.x >= GRID_WIDTH or position.y < 0 or position.y >= GRID_HEIGHT:
                            valid = False
                            break

                        # invalid if it overlaps with something else
                        if minimap_grid[position] is not None:
                            valid = False
                            break

                        # invalid if it's on the edge of map and wants to point out of bounds
                        if (
                            (position.x == 0 and tile_data[Direction.LEFT.value] == MapValue.DOOR.value)
                            or (
                                position.x == GRID_WIDTH - 1 and tile_data[Direction.RIGHT.value] == MapValue.DOOR.value
                            )
                            or (position.y == 0 and tile_data[Direction.UP.value] == MapValue.DOOR.value)
                            or (
                                position.y == GRID_HEIGHT - 1 and tile_data[Direction.DOWN.value] == MapValue.DOOR.value
                            )
                        ):
                            valid = False
                            break

                        # invalid if it's pointing into a room with a wall
                        up_pos = Point(position.x, position.y - 1)
                        right_pos = Point(position.x + 1, position.y)
                        down_pos = Point(position.x, position.y + 1)
                        left_pos = Point(position.x - 1, position.y)
                        if (
                            (
                                up_pos.y >= 0
                                and tile_data[Direction.UP.value] == MapValue.DOOR.value
                                and minimap_grid[up_pos]
                                and minimap_grid[up_pos][0][Direction.DOWN.value] == MapValue.WALL.value
                            )
                            or (
                                right_pos.x < GRID_WIDTH
                                and tile_data[Direction.RIGHT.value] == MapValue.DOOR.value
                                and minimap_grid[right_pos]
                                and minimap_grid[right_pos][0][Direction.LEFT.value] == MapValue.WALL.value
                            )
                            or (
                                down_pos.y < GRID_HEIGHT
                                and tile_data[Direction.DOWN.value] == MapValue.DOOR.value
                                and minimap_grid[down_pos]
                                and minimap_grid[down_pos][0][Direction.UP.value] == MapValue.WALL.value
                            )
                            or (
                                left_pos.x >= 0
                                and tile_data[Direction.LEFT.value] == MapValue.DOOR.value
                                and minimap_grid[left_pos]
                                and minimap_grid[left_pos][0][Direction.RIGHT.value] == MapValue.WALL.value
                            )
                        ):
                            valid = False
                            break

                        # invalid if another room with a door points into a current wall
                        if (
                            (
                                up_pos.y >= 0
                                and tile_data[Direction.UP.value] == MapValue.WALL.value
                                and minimap_grid[up_pos]
                                and minimap_grid[up_pos][0][Direction.DOWN.value] == MapValue.DOOR.value
                            )
                            or (
                                right_pos.x < GRID_WIDTH
                                and tile_data[Direction.RIGHT.value] == MapValue.WALL.value
                                and minimap_grid[right_pos]
                                and minimap_grid[right_pos][0][Direction.LEFT.value] == MapValue.DOOR.value
                            )
                            or (
                                down_pos.y < GRID_HEIGHT
                                and tile_data[Direction.DOWN.value] == MapValue.WALL.value
                                and minimap_grid[down_pos]
                                and minimap_grid[down_pos][0][Direction.UP.value] == MapValue.DOOR.value
                            )
                            or (
                                left_pos.x >= 0
                                and tile_data[Direction.LEFT.value] == MapValue.WALL.value
                                and minimap_grid[left_pos]
                                and minimap_grid[left_pos][0][Direction.RIGHT.value] == MapValue.DOOR.value
                            )
                        ):
                            valid = False
                            break

                        # TODO!!! Invalid if the doors in the rooms themselves don't match!

                    if valid:
                        valid_rooms.append((room, potential_position))

                return valid_rooms

            GRID_WIDTH = 74
            GRID_HEIGHT = 57
            # GRID_WIDTH = 10
            # GRID_HEIGHT = 10

            minimap_grid: dict[Point, None | tuple[str, Area]] = {
                Point(x, y): None for x in range(GRID_WIDTH) for y in range(GRID_HEIGHT)
            }

            def fill_grid_with_room(position: Point, room: Area):
                relative_x_offset, relative_y_offset = get_relative_offsets(room)
                minimap_data = room.extra["minimap_data"]
                for entry in minimap_data:
                    minimap_grid[
                        Point(position.x + entry["x"] - relative_x_offset, position.y + entry["y"] - relative_y_offset)
                    ] = entry["tile_data"], room

                    if len(minimap_grid) > (GRID_HEIGHT * GRID_WIDTH):
                        print(entry)

            def get_open_connections():
                connections = []
                for key, value in minimap_grid.items():
                    if value is None:
                        continue

                    up, right, down, left = value[0][0:4]

                    if key.x == 0 or key.y == 0 or key.x == GRID_WIDTH - 1 or key.x == GRID_HEIGHT - 1:
                        continue

                    if up == MapValue.DOOR.value and minimap_grid[key.x, key.y - 1] is None:
                        connections.append((key, Direction.DOWN))
                        continue

                    if right == MapValue.DOOR.value and minimap_grid[key.x + 1, key.y] is None:
                        connections.append((key, Direction.LEFT))
                        continue

                    if down == MapValue.DOOR.value and minimap_grid[key.x, key.y + 1] is None:
                        connections.append((key, Direction.UP))
                        continue

                    if left == MapValue.DOOR.value and minimap_grid[key.x - 1, key.y] is None:
                        connections.append((key, Direction.RIGHT))
                        continue

                return connections

            # start_position = Point(rng.randint(0, GRID_WIDTH-1), rng.randint(0, GRID_HEIGHT-1))
            start_position = Point(
                int(GRID_WIDTH / 2), int(GRID_HEIGHT / 2)
            )  # TODO: confirm that chosen room is in bounds?
            # start_position = Point(3, 3)

            rooms_to_use = list(game.region_list.all_areas)
            rooms_to_use.remove(
                game.region_list.area_by_area_location(
                    AreaIdentifier("Hydro Station", "Wave Beam Chamber Access Water Pipe")
                )
            )
            rooms_to_use.remove(
                game.region_list.area_by_area_location(
                    AreaIdentifier("Hydro Station", "Varia Chamber Access Water Pipe")
                )
            )
            rooms_to_use.remove(
                game.region_list.area_by_area_location(
                    AreaIdentifier("Hydro Station", "Autrack Corridor Left Water Pipe")
                )
            )
            rooms_to_use.remove(
                game.region_list.area_by_area_location(
                    AreaIdentifier("Hydro Station", "Autrack Corridor Right Water Pipe")
                )
            )
            rooms_to_use.remove(game.region_list.area_by_area_location(AreaIdentifier("GFS Thoth", "Elevator Shaft")))
            rooms_to_use.remove(
                game.region_list.area_by_area_location(AreaIdentifier("The Tower", "Power Plant Destroyed Shaft"))
            )  # TODO: temporarily, needs minimap data adjustment

            def pop_random_room():
                return rooms_to_use.pop(rng.randint(0, len(rooms_to_use)) - 1)

            def weight_for_room(room: Area):
                data = room.extra["minimap_data"]
                weight = 0
                for entry in data:
                    tiles = entry["tile_data"]
                    if tiles[Direction.UP.value] == MapValue.DOOR.value:
                        weight += 1
                    if tiles[Direction.RIGHT.value] == MapValue.DOOR.value:
                        weight += 1
                    if tiles[Direction.LEFT.value] == MapValue.DOOR.value:
                        weight += 1
                    if tiles[Direction.DOWN.value] == MapValue.DOOR.value:
                        weight += 1
                return -0.1 * iteration * weight + iteration + weight

            iteration = 0
            starting_room = pop_random_room()
            # starting_room = rooms_to_use.pop(1)

            fill_grid_with_room(start_position, starting_room)

            open_map_connections = get_open_connections()

            if len(open_map_connections) <= 0:
                raise ValueError(f"uh oh - {starting_room}")

            while len(open_map_connections) > 0:
                iteration += 1
                current_position, current_direction = open_map_connections.pop(0)
                current_tile_data, current_room = minimap_grid[current_position]
                match current_direction:
                    case Direction.UP:
                        current_position = Point(current_position.x, current_position.y + 1)
                    case Direction.RIGHT:
                        current_position = Point(current_position.x - 1, current_position.y)
                    case Direction.DOWN:
                        current_position = Point(current_position.x, current_position.y - 1)
                    case Direction.LEFT:
                        current_position = Point(current_position.x + 1, current_position.y)

                possible_rooms = get_possible_rooms(rooms_to_use, current_direction, current_position)
                weights = [weight_for_room(room) for room, pos in possible_rooms]

                random_room, current_position = rng.choices(possible_rooms, weights)[0]

                rooms_to_use.remove(random_room)
                fill_grid_with_room(current_position, random_room)

                open_map_connections = get_open_connections()

            create_mapper_dict()
            print(len(rooms_to_use))

        yield from dock_assignment
