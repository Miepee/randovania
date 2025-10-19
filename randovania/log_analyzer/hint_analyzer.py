from collections import defaultdict
from pathlib import Path

from tabulate import tabulate

from randovania.game_description.db.pickup_node import PickupNode
from randovania.game_description.hint import (
    HintFeature,
    HintLocationPrecision,
    HintType,
    SpecificHintPrecision,
)
from randovania.layout.layout_description import LayoutDescription


def create_report(seeds_dir: Path, output_file: Path, csv_dir: Path | None):
    hint_count = defaultdict(int)
    location_count = defaultdict(int)
    item_count = defaultdict(int)
    feature_with_room_count = defaultdict(lambda: defaultdict(int))
    cunt = 0
    for path in seeds_dir.glob("*.rdvgame"):
        # if cunt >= 50:
        #    break
        cunt += 1
        print(cunt)
        description = LayoutDescription.from_file(path)

        for player, game_mod in description.all_patches.items():
            # init with all locations if it hasnt been
            if not location_count:
                for pickup_node in game_mod.game.region_list.iterate_nodes_of_type(PickupNode):
                    identifier = pickup_node.identifier
                    location_count[f"{identifier.area}/{identifier.node}"] = 0

            for node_identifier, hint in game_mod.hints.items():
                if hint.hint_type() == HintType.RED_TEMPLE_KEY_SET:
                    # Ignore red temple keys
                    continue

                if hint.hint_type() == HintType.JOKE:
                    continue
                    hint_count["joke-hint"] += 1
                    continue
                if hint.hint_type() == HintType.LOCATION:
                    location_type = hint.precision.location
                    feature_name = ""
                    if isinstance(location_type, HintLocationPrecision):
                        feature_name = location_type.value
                    elif isinstance(location_type, HintFeature):
                        feature_name = location_type.name
                    elif isinstance(location_type, SpecificHintPrecision):
                        raise ValueError("Location is SpecificHintPrecision, this should never happen")

                    hint_count[feature_name] += 1

                    location = game_mod.game.region_list.node_from_pickup_index(hint.target).identifier
                    location_count[f"{location.area}/{location.node}"] += 1

                    feature_with_room_count[feature_name][location.as_string] += 1

                    item = game_mod.pickup_assignment[hint.target].pickup.name
                    item_count[item] += 1

    def sort_dict_by_value(d: dict) -> dict:
        return dict(sorted(d.items(), key=lambda element: element[1], reverse=True))

    def get_sum_of_values(d: dict) -> int:
        return sum(d.values())

    hint_count = sort_dict_by_value(hint_count)
    location_count = sort_dict_by_value(location_count)
    item_count = sort_dict_by_value(item_count)
    hint_sum = get_sum_of_values(hint_count)

    hint_list = []
    for hint, count in hint_count.items():
        hint_list.append([hint, count, f"{(count / cunt):.3%}", f"{(count / hint_sum):.3%}"])
    print("FEATURES")
    print(tabulate(hint_list, headers=["Features", "Count", "Rate of appearance", "Frequency"], tablefmt="grid"))

    print("==================")
    print("==================")
    print("==================")

    hint_list = []
    for item, count in item_count.items():
        hint_list.append([item, count, f"{(count / cunt):.3%}"])
    print("ITEMS")
    print(tabulate(hint_list, headers=["Hinted Item", "Count", "Frequency"], tablefmt="grid"))

    print("==================")
    print("==================")
    print("==================")

    hint_list = []
    for location, count in location_count.items():
        hint_list.append([location, count, f"{(count / cunt):.3%}"])
    print("LOCATIONS")
    print(tabulate(hint_list, headers=["Hinted Location", "Count", "Frequency"], tablefmt="grid"))

    print("==================")
    print("==================")
    print("==================")
    print("==================")
    print("##################")
    print("##################")
    print("##################")
    for feature, info in feature_with_room_count.items():
        hint_list = []
        total = sum(info.values())
        for room, count in sort_dict_by_value(info).items():
            hint_list.append([room, count, f"{(count / total):.3%}"])
        print(tabulate(hint_list, headers=[f"{feature}", "Count", "Frequency"], tablefmt="grid"))
        print("%%%%%%%%%%%%%%%%%%%%")

    inasdf = 0

    print(inasdf)
