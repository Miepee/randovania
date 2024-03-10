from __future__ import annotations

from randovania.exporter import item_names, pickup_exporter
from randovania.exporter.hints import guaranteed_item_hint
from randovania.exporter.hints.hint_exporter import HintExporter
from randovania.exporter.patch_data_factory import PatchDataFactory
from randovania.game_description.assignment import PickupTarget
from randovania.game_description.db.hint_node import HintNode
from randovania.games.fusion.exporter.hint_namer import FusionHintNamer
from randovania.games.game import RandovaniaGame
from randovania.generator.pickup_pool import pickup_creator


class FusionPatchDataFactory(PatchDataFactory):
    # TODO
    def game_enum(self) -> RandovaniaGame:
        return RandovaniaGame.FUSION

    def create_data(self) -> dict:
        db = self.game

        useless_target = PickupTarget(
            pickup_creator.create_nothing_pickup(db.resource_database, "Empty"),
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
            visual_nothing=pickup_creator.create_visual_nothing(self.game_enum(), "Anonymous"),
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
                        "ItemSprite": pickup.model.name,
                    }
                )

        tank_dict = {}
        for definition, state in self.patches.configuration.ammo_pickup_configuration.pickups_state.items():
            tank_dict[definition.extra["TankIncrementName"]] = state.ammo_count[0]
        tank_dict["EnergyTank"] = self.configuration.energy_per_tank

        starting_dict = {
            "Energy": self.configuration.energy_per_tank - 1,
            "Abilities": [],
            "SecurityLevels": [],
            "DownloadedMaps": [0, 1, 2, 3, 4, 5, 6],
        }
        missile_launcher = next(
            state
            for defi, state in self.configuration.standard_pickup_configuration.pickups_state.items()
            if defi.name == "Missile Launcher Data"
        )
        starting_dict["Missiles"] = missile_launcher.included_ammo[0]
        pb_launcher = next(
            state
            for defi, state in self.configuration.standard_pickup_configuration.pickups_state.items()
            if defi.name == "Power Bomb Data"
        )
        starting_dict["PowerBombs"] = pb_launcher.included_ammo[0]

        for item in self.patches.starting_equipment:
            if "Metroid" in item.name:
                print("skip metroid")
                continue
            pickup_def = next(
                value for key, value in self.pickup_db.standard_pickups.items() if value.name == item.name
            )
            category = pickup_def.extra["StartingItemCategory"]
            # Special Case for E-Tanks
            if category == "Energy":
                starting_dict[category] += self.configuration.energy_per_tank
                continue
            starting_dict[category].append(pickup_def.extra["StartingItemName"])

        starting_location_node = self.game.region_list.node_by_identifier(self.patches.starting_location)
        starting_location_dict = {
            "Area": self.game.region_list.nodes_to_region(starting_location_node).extra["area_id"],
            "Room": self.game.region_list.nodes_to_area(starting_location_node).extra["room_id"][0],
            "X": starting_location_node.extra["X"],
            "Y": starting_location_node.extra["Y"],
        }

        nav_text_json = {}
        hint_lang_list = ["JapaneseKanji", "JapaneseHiragana", "English", "German", "French", "Italian", "Spanish"]
        namer = FusionHintNamer(self.description.all_patches, self.players_config)
        exporter = HintExporter(namer, self.rng, ["A joke hint."])

        artifacts = [self.game.resource_database.get_item(f"Infant Metroid {i + 1}") for i in range(20)]

        metroid_hint_mapping = guaranteed_item_hint.create_guaranteed_hints_for_resources(
            self.description.all_patches,
            self.players_config,
            FusionHintNamer(self.description.all_patches, self.players_config),
            False,  # TODO: make this depending on hint settings later:tm:
            artifacts,
            True,
        )

        hints = {}
        for node in self.game.region_list.iterate_nodes():
            if not isinstance(node, HintNode):
                continue
            hints[node.extra["hint_name"]] = exporter.create_message_for_hint(
                self.patches.hints[self.game.region_list.identifier_for_node(node)],
                self.description.all_patches,
                self.players_config,
                True,
            ).strip()
            if node.extra["hint_name"] == "RestrictedLabs":
                hints[node.extra["hint_name"]] = " ".join(
                    [text for _, text in metroid_hint_mapping.items() if "has no need to be located" not in text]
                )

        starting_items_list = item_names.additional_starting_equipment(
            self.patches.configuration, self.patches.game, self.patches
        )
        starting_items_text = (
            "HQ has provided you with the following starting items: " + ", ".join(starting_items_list) + ". "
            if len(starting_items_list) > 0
            else ""
        )
        metroid_location_text = "anywhere" if self.configuration.artifacts.prefer_anywhere else "at bosses"
        for lang in hint_lang_list:
            nav_text_json[lang] = {
                "NavigationTerminals": hints,
                "ShipText": {
                    "InitialText": f"{starting_items_text}Your objective is as follows: the [COLOR=3]SA-X[/COLOR] has discovered and destroyed a top secret [COLOR=3]Metroid[/COLOR] breeding facility. It released {self.patches.configuration.artifacts.required_artifacts} infant Metroids into the station. Initial scans indicate that they are hiding {metroid_location_text}. Find and capture {self.patches.configuration.artifacts.required_artifacts} of them, to lure out the SA-X. Then initiate the station's self-destruct sequence. Uplink at [COLOR=2]Navigation Rooms[/COLOR] along the way. I can scan the station for useful equipment from there.[OBJECTIVE]Good. Move out.",
                    "ConfirmText": "Any Objections, Lady?",
                },
            }

        final_json = {
            "SeedHash": self.description.shareable_hash,
            "Locations": {
                "MajorLocations": major_pickup_list,
                "MinorLocations": minor_pickup_list,
            },
            "RequiredMetroidCount": self.configuration.artifacts.required_artifacts,
            "TankIncrements": tank_dict,
            "SkipDoorTransitions": False,  # TODO: make this available as a patch in-app
            "StartingItems": starting_dict,
            "StartingLocation": starting_location_dict,
            "NavigationText": nav_text_json,
        }
        import json

        foo = json.dumps(final_json)
        print(foo)

        return {}
