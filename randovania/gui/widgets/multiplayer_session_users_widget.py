import functools
import functools
import uuid

from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import Qt, Signal
from qasync import asyncSlot

from randovania.games.game import RandovaniaGame
from randovania.gui.dialog.select_preset_dialog import SelectPresetDialog
from randovania.gui.lib import async_dialog, common_qt_lib
from randovania.gui.lib.multiplayer_session_api import MultiplayerSessionApi
from randovania.interface_common.options import Options, InfoAlert
from randovania.interface_common.preset_manager import PresetManager
from randovania.layout import preset_describer
from randovania.network_client.multiplayer_session import MultiplayerSessionEntry, MultiplayerWorld
from randovania.network_common.session_state import MultiplayerSessionState


def make_tool(text: str):
    tool = QtWidgets.QToolButton()
    tool.setText(text)
    tool.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
    tool.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    return tool


def connect_to(action: QtGui.QAction, target, *args):
    if args:
        target = functools.partial(target, *args)
    action.triggered.connect(target)


class MultiplayerSessionUsersWidget(QtWidgets.QTreeWidget):
    GameExportRequested = Signal(RandovaniaGame, dict)

    _game_session: MultiplayerSessionEntry

    def __init__(self, options: Options, preset_manager: PresetManager, session_api: MultiplayerSessionApi):
        super().__init__()
        self.header().setStretchLastSection(False)
        self.headerItem().setText(0, "Name")
        self.headerItem().setText(1, "")
        self.headerItem().setText(2, "")
        self.headerItem().setText(3, "")
        self.header().setVisible(False)

        self._options = options
        self._preset_manager = preset_manager
        self._session_api = session_api

    @property
    def your_id(self) -> int | None:
        user = self._session_api.network_client.current_user
        if user is not None:
            return user.id
        return None

    #
    async def _prompt_for_preset(self, game: RandovaniaGame):
        dialog = SelectPresetDialog(game, self._preset_manager, self._options)

        result = await async_dialog.execute_dialog(dialog)
        if result == QtWidgets.QDialog.DialogCode.Accepted:
            return dialog.selected_preset
        else:
            return None

    #

    @asyncSlot()
    async def _preset_replace(self, game: RandovaniaGame, preset_id: uuid.UUID):
        preset = await self._prompt_for_preset(game)
        if preset is not None:
            await self._session_api.replace_preset_for(preset_id, preset)

    @asyncSlot()
    async def _preset_claim_with(self, preset_id: uuid.UUID, owner: int):
        await self._session_api.claim_preset_for(preset_id, owner)

    @asyncSlot()
    async def _preset_unclaim(self, preset_id: uuid.UUID):
        await self._session_api.unclaim_preset(preset_id)

    @asyncSlot()
    async def _preset_delete(self, preset_id: uuid.UUID):
        await self._session_api.delete_preset(preset_id)

    @asyncSlot()
    async def _preset_view_summary(self, preset_id: uuid.UUID):
        game = self._game_session.get_world(preset_id)
        preset = game.preset.get_preset()
        description = preset_describer.merge_categories(preset_describer.describe(preset))

        message_box = QtWidgets.QMessageBox(self)
        message_box.setWindowTitle(preset.name)
        message_box.setText(description)
        message_box.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        await async_dialog.execute_dialog(message_box)

    @asyncSlot()
    async def _preset_customize(self, preset_id: uuid.UUID):
        print("customize preset", preset_id)
        # if self._logic_settings_window is not None:
        #     if self._logic_settings_window._game_session_row == row:
        #         self._logic_settings_window.raise_()
        #         self._logic_settings_window.activateWindow()
        #     else:
        #         # show warning that a dialog is already in progress?
        #         await async_dialog.warning(self, "Customize in progress",
        #                                    "A window for customizing a preset is already open. "
        #                                    "Please close it before continuing.",
        #                                    async_dialog.StandardButton.Ok, async_dialog.StandardButton.Ok)
        #     return
        #
        # row_index = self.rows.index(row)
        # old_preset = self._game_session.presets[row_index].get_preset()
        # if self._preset_manager.is_included_preset_uuid(old_preset.uuid):
        #     old_preset = old_preset.fork()
        #
        # editor = PresetEditor(old_preset, self._options)
        # self._logic_settings_window = CustomizePresetDialog(self._window_manager, editor)
        # self._logic_settings_window.on_preset_changed(editor.create_custom_preset_with())
        # editor.on_changed = lambda: self._logic_settings_window.on_preset_changed(editor.create_custom_preset_with())
        # self._logic_settings_window._game_session_row = row
        #
        # result = await async_dialog.execute_dialog(self._logic_settings_window)
        # self._logic_settings_window = None
        #
        # if result == QtWidgets.QDialog.DialogCode.Accepted:
        #     new_preset = VersionedPreset.with_preset(editor.create_custom_preset_with())
        #
        #     if self._preset_manager.add_new_preset(new_preset):
        #         self.refresh_row_import_preset_actions()
        #
        #     await self._do_import_preset(row_index, new_preset)

    @asyncSlot()
    async def _preset_save_copy(self, preset_id: uuid.UUID):
        preset = self._game_session.get_world(preset_id).preset

        if preset.is_included_preset:
            # Nothing to do, this is an included preset
            return

        self._preset_manager.add_new_preset(preset)

    @asyncSlot()
    async def _preset_save_to_file(self, preset_id: uuid.UUID):
        path = common_qt_lib.prompt_user_for_preset_file(self, new_file=True)
        if path is None:
            return

        preset = self._game_session.get_world(preset_id).preset
        preset.save_to_file(path)

    @asyncSlot()
    async def _game_export(self, preset_id: uuid.UUID):
        options = self._options

        if not options.is_alert_displayed(InfoAlert.MULTIWORLD_FAQ):
            await async_dialog.message_box(self, QtWidgets.QMessageBox.Icon.Information, "Multiworld FAQ",
                                           "Have you read the Multiworld FAQ?\n"
                                           "It can be found in the main Randovania window → Help → Multiworld")
            options.mark_alert_as_displayed(InfoAlert.MULTIWORLD_FAQ)

        game_enum = self._game_session.get_world(preset_id).preset.game
        patch_data = await self._session_api.create_patcher_file(
            preset_id, options.options_for_game(game_enum).cosmetic_patches.as_json
        )
        self.GameExportRequested.emit(game_enum, patch_data)

    #

    @asyncSlot()
    async def _new_game(self, game: RandovaniaGame):
        preset = await self._prompt_for_preset(game)
        if preset is None:
            return

        you = [player for player in self._game_session.users if player.id == self.your_id][0]
        if not you.worlds:
            new_name = you.name
        else:
            new_name = f"{you.name} ({len(you.worlds) + 1})"

        # Temp
        await self._session_api.create_new_preset(new_name, preset)

    #

    def _fill_menu_for_game_replace_preset(self, menu: QtWidgets.QMenu, preset_id: uuid.UUID):
        for g in RandovaniaGame.all_games():
            connect_to(
                menu.addAction(g.long_name),
                self._preset_replace, g, preset_id,
            )

    def _fill_menu_for_new_game(self, menu: QtWidgets.QMenu):
        for g in RandovaniaGame.all_games():
            connect_to(
                menu.addAction(g.long_name),
                self._new_game, g,
            )

    def is_admin(self) -> bool:
        return any(
            player.admin and player.id == self.your_id
            for player in self._game_session.users
        )

    def update_state(self, game_session: MultiplayerSessionEntry):
        self.clear()

        self._game_session = game_session
        in_setup = self._game_session.state == MultiplayerSessionState.SETUP
        has_layout = self._game_session.game_details is not None

        game_by_id: dict[uuid.UUID, MultiplayerWorld] = {
            game.id: game
            for game in game_session.worlds
        }
        used_games = set()

        def _add_game(game_details: MultiplayerWorld, parent: QtWidgets.QTreeWidgetItem,
                      owner: int | None, game_state: str):
            game_item = QtWidgets.QTreeWidgetItem(parent)
            # game_item.setFlags(game_item.flags() | Qt.ItemFlag.ItemIsEditable)
            game_item.setText(0, game_details.name)
            game_item.setText(1, game_details.preset.game.long_name)
            game_item.setText(2, game_state)

            game_tool = make_tool("Actions")

            game_menu = QtWidgets.QMenu(game_tool)
            preset_menu = game_menu.addMenu(f"Preset: {game_details.preset.name}")
            connect_to(preset_menu.addAction("View summary"),
                       self._preset_view_summary, game_details.id)

            if owner == self.your_id or self.is_admin():
                customize_action = preset_menu.addAction("Customize")
                customize_action.setEnabled(not has_layout)
                connect_to(customize_action,
                           self._preset_customize, game_details.id)

                self._fill_menu_for_game_replace_preset(replace_with_menu := preset_menu.addMenu("Replace with"),
                                                        game_details.id)
                replace_with_menu.setEnabled(not has_layout)

            export_menu = preset_menu.addMenu("Export preset")
            connect_to(export_menu.addAction("Save copy of preset"), self._preset_save_copy, game_details.id)
            connect_to(export_menu.addAction("Save to file"), self._preset_save_to_file, game_details.id)

            if owner == self.your_id:
                export_action = game_menu.addAction("Export game")
                export_action.setEnabled(has_layout)
                connect_to(export_action, self._game_export, game_details.id)

            if owner is None:
                game_menu.addSeparator()
                connect_to(game_menu.addAction("Claim for yourself"),
                           self._preset_claim_with, game_details.id,
                           self.your_id)

                if self.is_admin():
                    claim_menu = game_menu.addMenu("Claim for")
                    for p in self._game_session.users:
                        connect_to(claim_menu.addAction(p.name),
                                   self._preset_claim_with, game_details.id,
                                   p.id)

            elif self.is_admin():
                game_menu.addSeparator()
                connect_to(game_menu.addAction("Unclaim"), self._preset_unclaim, game_details.id)

            if owner == self.your_id or self.is_admin():
                game_menu.addSeparator()
                delete_action = game_menu.addAction("Delete")
                delete_action.setEnabled(not has_layout)
                connect_to(delete_action, self._preset_delete, game_details.id)

            game_tool.setMenu(game_menu)

            self.setItemWidget(game_item, 3, game_tool)

        for player in game_session.users:
            item = QtWidgets.QTreeWidgetItem(self)
            item.setExpanded(True)
            item.setText(0, player.name)
            if player.admin:
                item.setText(1, "(Admin)")

            for preset_id, state in player.worlds.items():
                used_games.add(preset_id)
                _add_game(game_by_id[preset_id], item, player.id, state)

            if player.id != self.your_id and self.is_admin():
                tool = make_tool("Administrate")
                menu = QtWidgets.QMenu(tool)
                menu.addAction("Kick player")
                menu.addAction("Demote from Admin" if player.admin else "Promote to Admin")
                tool.setMenu(menu)
                self.setItemWidget(item, 3, tool)

            if in_setup and (player.id == self.your_id or self.is_admin()):
                new_game_item = QtWidgets.QTreeWidgetItem(item)
                tool = make_tool("New game")
                menu = QtWidgets.QMenu(tool)
                self._fill_menu_for_new_game(menu)
                tool.setMenu(menu)
                self.setItemWidget(new_game_item, 0, tool)

        missing_games = set(game_by_id.keys()) - used_games
        if missing_games:
            missing_game_item = QtWidgets.QTreeWidgetItem(self)
            missing_game_item.setExpanded(True)
            missing_game_item.setText(0, "Unclaimed Games")

            for preset_id in missing_games:
                _add_game(game_by_id[preset_id], missing_game_item, None, "Abandoned")

        self.resizeColumnToContents(0)
        self.resizeColumnToContents(1)
