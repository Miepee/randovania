from __future__ import annotations

import functools
import typing
import uuid

from PySide6 import QtWidgets, QtCore

from randovania.gui.lib import async_dialog
from randovania.gui.lib.qt_network_client import QtNetworkClient
from randovania.layout.versioned_preset import VersionedPreset
from randovania.network_client.multiplayer_session import (
    MultiplayerUser, MultiplayerSessionEntry, MultiplayerWorldActions,
    MultiplayerPickups, MultiplayerSessionAuditLog, WorldUserInventory
)
from randovania.network_client.network_client import UnableToConnect
from randovania.network_common.admin_actions import SessionAdminUserAction
from randovania.network_common.error import (
    InvalidAction, ServerError, NotLoggedIn, NotAuthorizedForAction,
    UserNotAuthorized, UnsupportedClient, RequestTimeout
)

Param = typing.ParamSpec("Param")
RetType = typing.TypeVar("RetType")
OriginalFunc = typing.Callable[Param, RetType]


def handle_network_errors(fn: typing.Callable[typing.Concatenate[MultiplayerSessionApi, Param], RetType]
                          ) -> typing.Callable[Param, RetType]:
    @functools.wraps(fn)
    async def wrapper(self: MultiplayerSessionApi, *args, **kwargs):
        parent = self.widget_root
        try:
            return await fn(self, *args, **kwargs)

        except InvalidAction as e:
            await async_dialog.warning(parent, "Invalid action", f"{e}")

        except ServerError:
            await async_dialog.warning(parent, "Server error",
                                       "An error occurred on the server while processing your request.")

        except NotLoggedIn:
            await async_dialog.warning(parent, "Unauthenticated",
                                       "You must be logged in.")

        except NotAuthorizedForAction:
            await async_dialog.warning(parent, "Unauthorized",
                                       "You're not authorized to perform that action.")

        except UserNotAuthorized:
            await async_dialog.warning(
                parent, "Unauthorized",
                "You're not authorized to use this build.\nPlease check #dev-builds for more details.",
            )

        except UnsupportedClient as e:
            s = e.detail.replace('\n', '<br />')
            await async_dialog.warning(
                parent, "Unsupported client",
                s,
            )

        except UnableToConnect as e:
            s = e.reason.replace('\n', '<br />')
            await async_dialog.warning(parent, "Connection Error",
                                       f"<b>Unable to connect to the server:</b><br /><br />{s}")

        except RequestTimeout as e:
            await async_dialog.warning(parent, "Connection Error",
                                       f"<b>Timeout while communicating with the server:</b><br /><br />{e}"
                                       f"<br />Further attempts will wait for longer.")

        return None

    return wrapper


class MultiplayerSessionApi(QtCore.QObject):
    MetaUpdated = QtCore.Signal(MultiplayerSessionEntry)
    ActionsUpdated = QtCore.Signal(MultiplayerWorldActions)
    AuditLogUpdated = QtCore.Signal(MultiplayerSessionAuditLog)
    InventoryUpdated = QtCore.Signal(WorldUserInventory)

    current_entry: MultiplayerSessionEntry
    widget_root: QtWidgets.QWidget | None

    def __init__(self, network_client: QtNetworkClient, entry: MultiplayerSessionEntry):
        super().__init__()
        self.widget_root = None
        self.network_client = network_client
        self.current_entry = entry

    async def _admin_user_action(self, player: MultiplayerUser, action: SessionAdminUserAction, arg):
        # self.setEnabled(False)
        try:
            return await self.network_client.session_admin_player(player.id, action, arg)
        finally:
            pass
            # self.setEnabled(True)

    @handle_network_errors
    async def replace_preset_for(self, preset_id: uuid.UUID, preset: VersionedPreset):
        # self._game_session.get_game(preset_id).preset = preset
        print(f"{preset.name} for {preset_id}")

    @handle_network_errors
    async def claim_preset_for(self, preset_id: uuid.UUID, owner: int):
        # for p in self._game_session.players:
        #     if p.id == owner:
        #         p.games[preset_id] = "Disconnected"

        print(f"Will claim {preset_id} to {owner}")

    @handle_network_errors
    async def unclaim_preset(self, preset_id: uuid.UUID):
        # for p in self._game_session.players:
        #     p.games.pop(preset_id, None)

        print(f"Will unclaim {preset_id}")

    @handle_network_errors
    async def delete_preset(self, preset_id: uuid.UUID):
        # self._game_session.games.remove(self._game_session.get_game(preset_id))
        #
        # for p in self._game_session.players:
        #     p.games.pop(preset_id, None)

        print(f"Will delete {preset_id}")

    @handle_network_errors
    async def create_new_preset(self, name: str, preset: VersionedPreset, owner: int | None):
        print(f"Create game named {name}")

        # new_preset_id = uuid.uuid4()
        # self._game_session.games.append(SessionGame(
        #     id=new_preset_id,
        #     name=new_name,
        #     preset=preset,
        # ))
        # you.games[new_preset_id] = "Disconnected"

    async def create_patcher_file(self, preset_id, as_json):
        pass
