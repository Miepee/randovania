import base64
import logging
import uuid

import flask_socketio
import peewee

from randovania.bitpacking import bitpacking
from randovania.game_description import default_database
from randovania.game_description.assignment import PickupTarget
from randovania.game_description.resources.pickup_entry import PickupEntry
from randovania.game_description.resources.pickup_index import PickupIndex
from randovania.game_description.resources.resource_database import ResourceDatabase
from randovania.layout.layout_description import LayoutDescription
from randovania.network_common import signals
from randovania.network_common.error import InvalidAction
from randovania.network_common.pickup_serializer import BitPackPickupEntry
from randovania.network_common.session_state import MultiplayerSessionState
from randovania.server.database import (
    World, WorldUserAssociation, MultiplayerSession,
    WorldAction
)
from randovania.server.lib import logger
from randovania.server.multiplayer import session_common
from randovania.server.server_app import ServerApp


def _base64_encode_pickup(pickup: PickupEntry, resource_database: ResourceDatabase) -> str:
    encoded_pickup = bitpacking.pack_value(BitPackPickupEntry(pickup, resource_database))
    return base64.b85encode(encoded_pickup).decode("utf-8")


def _get_resource_database(description: LayoutDescription, player: int) -> ResourceDatabase:
    return default_database.resource_database_for(description.get_preset(player).game)


def _get_pickup_target(description: LayoutDescription, provider: int, location: int) -> PickupTarget | None:
    pickup_assignment = description.all_patches[provider].pickup_assignment
    return pickup_assignment.get(PickupIndex(location))


def _collect_location(session: MultiplayerSession, world: World,
                      description: LayoutDescription,
                      pickup_location: int) -> World | None:
    """
    Collects the pickup in the given location. Returns
    :param session:
    :param world:
    :param description:
    :param pickup_location:
    :return: The rewarded player if some player must be updated of the fact.
    """
    pickup_target = _get_pickup_target(description, world.order, pickup_location)

    def log(msg):
        logger().info(f"{session_common.describe_session(session, world)} found item at {pickup_location}. {msg}")

    if pickup_target is None:
        log("It's nothing.")
        return None

    if pickup_target.player == world.order:
        log(f"It's a {pickup_target.pickup.name} for themselves.")
        return None

    target_world = World.get_by_order(session.id, pickup_target.player)

    try:
        WorldAction.create(
            provider=world,
            location=pickup_location,

            session=session,
            receiver=target_world,
        )
    except peewee.IntegrityError:
        # Already exists, and it's for another player, no inventory update needed
        log(f"It's a {pickup_target.pickup.name} for {target_world.name}, but it was already collected.")
        return None

    log(f"It's a {pickup_target.pickup.name} for {target_world.name}.")
    return target_world


def collect_locations(sio: ServerApp, world_uid: uuid.UUID, pickup_locations: tuple[int, ...]):
    current_user = sio.get_current_user()
    world = World.get_by_uuid(world_uid)
    session = world.session

    if session.state != MultiplayerSessionState.IN_PROGRESS:
        raise InvalidAction("Unable to collect locations of sessions that aren't in progress")

    try:
        WorldUserAssociation.get_by_ids(
            world_uid=world_uid,
            user_id=current_user.id,
        )
    except peewee.DoesNotExist:
        raise InvalidAction("This world was not claimed by you")

    logger().info(f"{session_common.describe_session(session, world)} found items {pickup_locations}")
    description = session.layout_description

    receiver_worlds = set()
    for location in pickup_locations:
        world = _collect_location(session, world, description, location)
        if world is not None:
            receiver_worlds.add(world)

    if not receiver_worlds:
        return

    for world in receiver_worlds:
        emit_game_session_pickups_update(sio, world)
    session_common.emit_session_actions_update(session)


def update_association(sio: ServerApp, world_uid: uuid.UUID, inventory: bytes | None, game_connection_state: str):
    current_user = sio.get_current_user()
    association = WorldUserAssociation.get_by_ids(
        world_uid=world_uid,
        user_id=current_user.id,
    )
    session = association.world.session

    old_state = association.connection_state
    old_inventory = association.inventory

    association.connection_state = f"Online, {game_connection_state}"
    if session.state == MultiplayerSessionState.IN_PROGRESS and inventory is not None:
        association.inventory = inventory

    association.save()

    if old_inventory != association.inventory and session.state == MultiplayerSessionState.IN_PROGRESS:
        session_common.emit_inventory_update(association)

    if old_state != association.connection_state:
        logger().info(
            "%s has new connection state: %s",
            session_common.describe_session(session, association.world), association.connection_state,
        )
        session_common.emit_session_meta_update(session)


def watch_inventory(sio: ServerApp, world_uid: uuid.UUID, user_id: int, watch: bool, binary: bool):
    if watch:
        # current_user = sio.get_current_user()
        # TODO: check if current user belongs to the same session

        association = WorldUserAssociation.get_by_ids(
            world_uid=world_uid,
            user_id=user_id,
        )

        flask_socketio.join_room(session_common.get_inventory_room_name(association))
        session_common.emit_inventory_update(association)
    else:
        # Allow one to stop listening even if you're not allowed to start listening
        flask_socketio.leave_room(session_common.get_inventory_room_name_raw(world_uid, user_id))


def emit_game_session_pickups_update(sio: ServerApp, world: World):
    session = world.session

    if session.state == MultiplayerSessionState.SETUP:
        raise RuntimeError("Unable to emit pickups during SETUP")

    description = session.layout_description
    resource_database = _get_resource_database(description, world.order)

    result = []
    actions: list[WorldAction] = WorldAction.select().where(
        WorldAction.receiver == world).order_by(WorldAction.time.asc())

    for action in actions:
        pickup_target = _get_pickup_target(description, action.provider.order,
                                           action.location)

        if pickup_target is None:
            logging.error(f"Action {action} has a location index with nothing.")
            result.append(None)
        else:
            result.append({
                "provider_name": action.provider.name,
                "pickup": _base64_encode_pickup(pickup_target.pickup, resource_database),
            })

    logger().info(f"{session_common.describe_session(session, world)} "
                  f"notifying {resource_database.game_enum.value} of {len(result)} pickups.")

    data = {
        "world": str(world.uuid),
        "game": resource_database.game_enum.value,
        "pickups": result,
    }
    flask_socketio.emit(signals.WORLD_PICKUPS_UPDATE, data, room=f"world-{world.uuid}")


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


def setup_app(sio: ServerApp):
    sio.on("multiplayer/collect_locations", collect_locations)
    sio.on("multiplayer/update_association", update_association)
    sio.on("multiplayer/watch_inventory", watch_inventory)
