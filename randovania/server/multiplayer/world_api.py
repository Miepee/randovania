import logging
import uuid

from randovania.network_common.error import InvalidAction
from randovania.network_common.session_state import MultiplayerSessionState
from randovania.server.database import MultiplayerMembership, World, WorldUserAssociation, MultiplayerSession
from randovania.server.lib import logger
from randovania.server.multiplayer import session_common
from randovania.server.server_app import ServerApp


def game_session_collect_locations(sio: ServerApp, session_id: int, pickup_locations: tuple[int, ...]):
    current_user = sio.get_current_user()
    session: MultiplayerSession = MultiplayerSession.get_by_id(session_id)
    membership = MultiplayerMembership.get_by_ids(current_user.id, session_id)

    if session.state != MultiplayerSessionState.IN_PROGRESS:
        raise InvalidAction("Unable to collect locations of sessions that aren't in progress")

    if membership.is_observer:
        raise InvalidAction("Observers can't collect locations")

    logger().info(f"{session_common.describe_session(session, membership)} found items {pickup_locations}")
    description = session.layout_description

    receiver_players = set()
    for location in pickup_locations:
        receiver_player = _collect_location(session, membership, description, location)
        if receiver_player is not None:
            receiver_players.add(receiver_player)

    if not receiver_players:
        return

    for receiver_player in receiver_players:
        try:
            receiver_membership = MultiplayerMembership.get_by_session_position(session, row=receiver_player)
            session_common.emit_game_session_pickups_update(sio, receiver_membership)
        except peewee.DoesNotExist:
            pass
    session_common.emit_session_actions_update(session)


def world_self_update(sio: ServerApp, world_id: uuid.UUID, inventory: bytes | None, game_connection_state: str):
    current_user = sio.get_current_user()
    world = World.get_by_uuid(world_id)
    session = world.session

    old_state = world.connection_state
    old_inventory = world.inventory

    world.connection_state = f"Online, {game_connection_state}"
    if session.state == MultiplayerSessionState.IN_PROGRESS and inventory is not None:
        world.inventory = inventory

    world.save()

    if old_inventory != world.inventory and session.state == MultiplayerSessionState.IN_PROGRESS:
        session_common.emit_inventory_update(world)

    if old_state != world.connection_state:
        logger().info(
            "%s has new connection state: %s",
            session_common.describe_session(session, world), world.connection_state,
        )
        session_common.emit_session_meta_update(session)


def setup_app(sio: ServerApp):
    sio.on("game_session_collect_locations", game_session_collect_locations)
    sio.on("game_session_self_update", world_self_update)


def report_disconnect(sio: ServerApp, session_dict: dict, log: logging.Logger):
    user_id: int | None = session_dict.get("user-id")
    if user_id is None:
        return

    # TODO: keep track of which worlds this given connection is updating
    # since we want to properly support multiple clients for one user

    associations: list[WorldUserAssociation] = list(WorldUserAssociation.select().where(
        WorldUserAssociation.user == user_id,
        WorldUserAssociation.connection_state != "Offline",
    ))

    log.info(f"User {user_id} is disconnected, disconnecting from sessions: {associations}")
    sessions_to_update = {}

    for association in associations:
        association.connection_state = "Offline"
        session = association.world.session
        sessions_to_update[session.id] = session
        association.save()

    for session in sessions_to_update.values():
        session_common.emit_session_meta_update(session)
