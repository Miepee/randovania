from __future__ import annotations

from randovania.exporter import pickup_exporter
from randovania.exporter.patch_data_factory import PatchDataFactory
from randovania.game_description.assignment import PickupTarget
from randovania.games.game import RandovaniaGame
from randovania.generator.pickup_pool import pickup_creator


class FusionPatchDataFactory(PatchDataFactory):
    # TODO
    def game_enum(self) -> RandovaniaGame:
        return RandovaniaGame.FUSION

    def create_data(self) -> dict:
        db = self.game

        useless_target = PickupTarget(
            pickup_creator.create_nothing_pickup(db.resource_database, "useless target sprite"),
            self.players_config.player_index,
        )

        pickup_list = pickup_exporter.export_all_indices(
            self.patches,
            useless_target,
            self.game.region_list,
            self.rng,
            self.configuration.pickup_model_style,
            self.configuration.pickup_model_data_source,
            exporter=pickup_exporter.create_pickup_exporter(
                pickup_exporter.GenericAcquiredMemo(), self.players_config, self.game_enum()
            ),
            visual_nothing=pickup_creator.create_visual_nothing(self.game_enum(), "Visual nothing sprite"),
        )

        minor_pickup_list = []
        major_pickup_list = []

        for pickup in pickup_list:
            node = self.game.region_list.node_from_pickup_index(pickup.index)
            print(str(node))
            is_major = False
            if "source" in node.extra:
                is_major = True

            resource = (
                pickup.conditional_resources[0].resources[-1][0].extra["item"] if not pickup.other_player else "None"
            )
            if is_major:
                major_pickup_list.append({"Source": node.extra["source"], "Item": resource})
            else:
                minor_pickup_list.append(
                    {
                        "Area": self.game.region_list.nodes_to_region(node).extra["area_id"],
                        "Room": self.game.region_list.nodes_to_area(node).extra["room_id"][0],
                        "BlockX": node.extra["blockx"],
                        "BlockY": node.extra["blocky"],
                        "Item": resource,
                    }
                )

        final_json = {
            "Locations": {
                "MajorLocations": major_pickup_list,
                "MinorLocations": minor_pickup_list,
            }
        }
        import json

        foo = json.dumps(final_json)
        print(foo)

        return {}
