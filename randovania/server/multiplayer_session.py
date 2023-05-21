import base64
import hashlib
import json
import logging
import typing
import uuid

import flask_socketio
import peewee

import randovania
from randovania.bitpacking import bitpacking
from randovania.game_description import default_database
from randovania.game_description.assignment import PickupTarget
from randovania.game_description.resources.pickup_entry import PickupEntry
from randovania.game_description.resources.pickup_index import PickupIndex
from randovania.game_description.resources.resource_database import ResourceDatabase
from randovania.interface_common.players_configuration import PlayersConfiguration
from randovania.layout.layout_description import LayoutDescription
from randovania.layout.versioned_preset import VersionedPreset
from randovania.network_common.admin_actions import SessionAdminGlobalAction, SessionAdminUserAction
from randovania.network_common.error import (NotAuthorizedForAction, InvalidAction)
from randovania.network_common.pickup_serializer import BitPackPickupEntry
from randovania.network_common.session_state import MultiplayerSessionState
from randovania.server import database
from randovania.server.database import (MultiplayerSession, MultiplayerMembership, WorldAction, World,
                                        MultiplayerAuditEntry, is_boolean, WorldUserAssociation)
from randovania.server.lib import logger
from randovania.server.multiplayer.session_common import describe_session, \
    emit_session_meta_update, add_audit_entry
from randovania.server.server_app import ServerApp


# def disconnect_game_session(sio: ServerApp, session_id: int):
#     current_user = sio.get_current_user()
#     try:
#         current_membership = MultiplayerMembership.get_by_ids(current_user.id, session_id)
#         current_membership.connection_state = "Offline"
#         current_membership.save()
#         emit_session_meta_update(current_membership.session)
#     except peewee.DoesNotExist:
#         pass
#     sio.leave_game_session()


def _verify_has_admin(sio: ServerApp, session_id: int, admin_user_id: int | None,
                      *, allow_when_no_admins: bool = False) -> None:
    """
    Checks if the logged user can do admin operations to the given session,
    :param session_id: The GameSessions id
    :param admin_user_id: An user id that is exceptionally authorized for this
    :param allow_when_no_admins: This action is authorized for non-admins if there are no admins.
    :return:
    """
    current_user = sio.get_current_user()
    try:
        current_membership = MultiplayerMembership.get_by_ids(current_user.id, session_id)
    except peewee.DoesNotExist:
        raise NotAuthorizedForAction()

    if not (current_membership.admin or (admin_user_id is not None and current_user.id == admin_user_id)):
        if allow_when_no_admins and MultiplayerMembership.select().where(
                MultiplayerMembership.session == session_id,
                is_boolean(MultiplayerMembership.admin, True)
        ).count() == 0:
            return
        raise NotAuthorizedForAction()


def _verify_in_setup(session: MultiplayerSession):
    if session.state != MultiplayerSessionState.SETUP:
        raise InvalidAction("Session is not in setup")


def _verify_no_layout_description(session: MultiplayerSession):
    if session.layout_description_json is not None:
        raise InvalidAction("Session has a generated game")


def _verify_not_in_generation(session: MultiplayerSession):
    if session.generation_in_progress is not None:
        raise InvalidAction("Session game is being generated")


def _get_preset(preset_json: dict) -> VersionedPreset:
    try:
        preset = VersionedPreset(preset_json)
        preset.get_preset()  # test if valid
        return preset
    except Exception as e:
        raise InvalidAction(f"invalid preset: {e}")


def _create_world(sio: ServerApp, session: MultiplayerSession, arg: tuple[str, dict]):
    if len(arg) != 2:
        raise InvalidAction("Missing arguments.")
    _verify_has_admin(sio, session.id, None)
    _verify_in_setup(session)
    _verify_no_layout_description(session)
    _verify_not_in_generation(session)
    name, preset_json = arg
    preset = _get_preset(preset_json)

    with database.db.atomic():
        logger().info(f"{describe_session(session)}: Creating world {name}.")
        World.create(session=session, name=name,
                     preset=json.dumps(preset.as_json))


def _change_world(sio: ServerApp, session: MultiplayerSession, arg: tuple[uuid.UUID, dict]):
    if len(arg) != 2:
        raise InvalidAction("Missing arguments.")
    world_uid, preset_json = arg
    _verify_has_admin(sio, session.id, sio.get_current_user().id)
    _verify_in_setup(session)
    _verify_no_layout_description(session)
    _verify_not_in_generation(session)
    preset = _get_preset(preset_json)

    if preset.game not in session.allowed_games:
        raise InvalidAction(f"Only {preset.game} preset not allowed.")

    if not randovania.is_dev_version() and preset.get_preset().configuration.unsupported_features():
        raise InvalidAction("Preset uses unsupported features.")

    try:
        with database.db.atomic():
            world = World.get_by_uuid(world_uid)
            world.preset = json.dumps(preset.as_json)
            logger().info(f"{describe_session(session)}: Changing world {world_uid}.")
            world.save()

    except peewee.DoesNotExist:
        raise InvalidAction(f"invalid world: {world_uid}")


def _delete_world(sio: ServerApp, session: MultiplayerSession, world_uid: uuid.UUID):
    _verify_has_admin(sio, session.id, None)
    _verify_in_setup(session)
    _verify_no_layout_description(session)
    _verify_not_in_generation(session)

    world = World.get_by_uuid(world_uid)
    with database.db.atomic():
        logger().info(f"{describe_session(session)}: Deleting {world_uid}.")
        WorldUserAssociation.delete().where(WorldUserAssociation.world == world.id).execute()
        World.delete().where(World.id == world.id).execute()


def _update_layout_generation(sio: ServerApp, session: MultiplayerSession, world_order: list[int]):
    _verify_has_admin(sio, session.id, None)
    _verify_in_setup(session)

    world_objects: dict[int, World] = {
        world.id: world
        for world in session.worlds
    }
    if world_order:
        used_ids = set(world_objects.keys())
        for world_id in world_order:
            if world_id not in used_ids:
                raise InvalidAction(f"World {world_id} duplicated in order, or unknown.")
            used_ids.remove(world_id)

        if used_ids:
            raise InvalidAction(f"Expected {len(world_objects)} worlds, got {len(world_order)}.")

        if session.generation_in_progress is not None:
            raise InvalidAction(f"Generation already in progress by {session.generation_in_progress.name}.")

    with database.db.atomic():
        if world_order:
            session.generation_in_progress = sio.get_current_user()
            for i, world_id in enumerate(world_order):
                world_objects[world_id].order = i
                world_objects[world_id].save()
        else:
            session.generation_in_progress = None

        logger().info(
            f"{describe_session(session)}: Making generation in progress to {session.generation_in_progress}."
        )
        session.save()


def _get_sorted_worlds(session: MultiplayerSession) -> list[World]:
    return list(World.select().where(World.session == session
                                     ).order_by(World.session.asc()))


def _change_layout_description(sio: ServerApp, session: MultiplayerSession, description_json: dict | None):
    _verify_has_admin(sio, session.id, None)
    _verify_in_setup(session)
    worlds_to_update = []

    if description_json is None:
        description = None
    else:
        if session.generation_in_progress != sio.get_current_user():
            if session.generation_in_progress is None:
                raise InvalidAction("Not waiting for a layout.")
            else:
                raise InvalidAction(f"Waiting for a layout from {session.generation_in_progress.name}.")

        _verify_no_layout_description(session)
        description = LayoutDescription.from_json_dict(description_json)
        worlds = _get_sorted_worlds(session)

        if description.player_count != len(worlds):
            raise InvalidAction(f"Description is for a {description.player_count} players,"
                                f" while the session is for {len(worlds)}.")

        for permalink_preset, world in zip(description.all_presets, worlds):
            if _get_preset(json.loads(world.preset)).get_preset() != permalink_preset:
                preset = VersionedPreset.with_preset(permalink_preset)
                if preset.game not in session.allowed_games:
                    raise InvalidAction(f"{preset.game} preset not allowed.")
                if not randovania.is_dev_version() and permalink_preset.configuration.unsupported_features():
                    raise InvalidAction(f"Preset {permalink_preset.name} uses unsupported features.")
                world.preset = json.dumps(preset.as_json)
                worlds_to_update.append(world)

    with database.db.atomic():
        for preset_row in worlds_to_update:
            preset_row.save()

        session.generation_in_progress = None
        session.layout_description = description
        session.save()
        add_audit_entry(sio, session,
                        "Removed generated game" if description is None
                        else f"Set game to {description.shareable_word_hash}")


def _download_layout_description(sio: ServerApp, session: MultiplayerSession):
    try:
        # You must be a session member to do get the spoiler
        MultiplayerMembership.get_by_ids(sio.get_current_user().id, session.id)
    except peewee.DoesNotExist:
        raise NotAuthorizedForAction()

    if session.layout_description_json is None:
        raise InvalidAction("Session does not contain a game")

    if not session.layout_description.has_spoiler:
        raise InvalidAction("Session does not contain a spoiler")

    add_audit_entry(sio, session, "Requested the spoiler log")
    return session.layout_description_json


def _start_session(sio: ServerApp, session: MultiplayerSession):
    _verify_has_admin(sio, session.id, None)
    _verify_in_setup(session)
    if session.layout_description_json is None:
        raise InvalidAction("Unable to start session, no game is available.")

    num_players = MultiplayerMembership.select().where(MultiplayerMembership.session == session,
                                                       MultiplayerMembership.row.is_null(False)).count()
    expected_players = session.num_rows
    if num_players != expected_players:
        raise InvalidAction(f"Unable to start session, there are {num_players} but expected {expected_players} "
                            f"({session.num_rows} x {session.num_teams}).")

    session.state = MultiplayerSessionState.IN_PROGRESS
    logger().info(f"{describe_session(session)}: Starting session.")
    session.save()
    add_audit_entry(sio, session, "Started session")


def _finish_session(sio: ServerApp, session: MultiplayerSession):
    _verify_has_admin(sio, session.id, None)
    if session.state != MultiplayerSessionState.IN_PROGRESS:
        raise InvalidAction("Session is not in progress")

    session.state = MultiplayerSessionState.FINISHED
    logger().info(f"{describe_session(session)}: Finishing session.")
    session.save()
    add_audit_entry(sio, session, "Finished session")


def _reset_session(sio: ServerApp, session: MultiplayerSession):
    raise InvalidAction("Restart session is not yet implemented.")


def _hash_password(password: str) -> str:
    return hashlib.blake2s(password.encode("utf-8")).hexdigest()


def _change_password(sio: ServerApp, session: MultiplayerSession, password: str):
    _verify_has_admin(sio, session.id, None)

    session.password = _hash_password(password)
    logger().info(f"{describe_session(session)}: Changing password.")
    session.save()
    add_audit_entry(sio, session, "Changed password")


def _change_title(sio: ServerApp, session: MultiplayerSession, title: str):
    _verify_has_admin(sio, session.id, None)

    old_name = session.name
    session.name = title
    logger().info(f"{describe_session(session)}: Changed name from {old_name}.")
    session.save()
    add_audit_entry(sio, session, f"Changed name from {old_name} to {title}")


def _duplicate_session(sio: ServerApp, session: MultiplayerSession, new_title: str):
    _verify_has_admin(sio, session.id, None)

    current_user = sio.get_current_user()
    add_audit_entry(sio, session, f"Duplicated session as {new_title}")

    with database.db.atomic():
        new_session: MultiplayerSession = MultiplayerSession.create(
            name=new_title,
            password=session.password,
            creator=current_user,
            layout_description_json=session.layout_description_json,
            seed_hash=session.seed_hash,
            dev_features=session.dev_features,
        )
        for preset in session.presets:
            assert isinstance(preset, World)
            World.create(
                session=new_session,
                row=preset.row,
                preset=preset.preset,
            )
        MultiplayerMembership.create(
            user=current_user,
            session=new_session,
            row=None, admin=True,
            connection_state="Offline",
        )
        MultiplayerAuditEntry.create(
            session=new_session,
            user=current_user,
            message=f"Duplicated from {session.name}",
        )


def _get_permalink(sio: ServerApp, session: MultiplayerSession) -> str:
    _verify_has_admin(sio, session.id, None)
    add_audit_entry(sio, session, "Requested permalink")

    return session.layout_description.permalink.as_base64_str


def game_session_admin_session(sio: ServerApp, session_id: int, action: str, arg):
    action: SessionAdminGlobalAction = SessionAdminGlobalAction(action)
    session: database.MultiplayerSession = database.MultiplayerSession.get_by_id(session_id)

    if action == SessionAdminGlobalAction.CREATE_WORLD:
        _create_world(sio, session, arg)

    elif action == SessionAdminGlobalAction.CHANGE_WORLD:
        _change_world(sio, session, arg)

    elif action == SessionAdminGlobalAction.DELETE_WORLD:
        _delete_world(sio, session, arg)

    elif action == SessionAdminGlobalAction.UPDATE_LAYOUT_GENERATION:
        _update_layout_generation(sio, session, arg)

    elif action == SessionAdminGlobalAction.CHANGE_LAYOUT_DESCRIPTION:
        _change_layout_description(sio, session, arg)

    elif action == SessionAdminGlobalAction.DOWNLOAD_LAYOUT_DESCRIPTION:
        return _download_layout_description(sio, session)

    elif action == SessionAdminGlobalAction.START_SESSION:
        _start_session(sio, session)

    elif action == SessionAdminGlobalAction.FINISH_SESSION:
        _finish_session(sio, session)

    elif action == SessionAdminGlobalAction.RESET_SESSION:
        _reset_session(sio, session)

    elif action == SessionAdminGlobalAction.CHANGE_PASSWORD:
        _change_password(sio, session, arg)

    elif action == SessionAdminGlobalAction.CHANGE_TITLE:
        _change_title(sio, session, arg)

    elif action == SessionAdminGlobalAction.DUPLICATE_SESSION:
        return _duplicate_session(sio, session, arg)

    elif action == SessionAdminGlobalAction.DELETE_SESSION:
        logger().info(f"{describe_session(session)}: Deleting session.")
        session.delete_instance(recursive=True)

    elif action == SessionAdminGlobalAction.REQUEST_PERMALINK:
        return _get_permalink(sio, session)

    emit_session_meta_update(session)


def _find_empty_row(session: MultiplayerSession) -> int:
    possible_rows = set(range(session.num_rows))
    for member in MultiplayerMembership.non_observer_members(session):
        possible_rows.remove(member.row)

    for empty_row in sorted(possible_rows):
        return empty_row
    raise InvalidAction("Session is full")


def game_session_admin_player(sio: ServerApp, session_id: int, user_id: int, action: str, arg):
    _verify_has_admin(sio, session_id, user_id)
    action: SessionAdminUserAction = SessionAdminUserAction(action)

    session: MultiplayerSession = database.MultiplayerSession.get_by_id(session_id)
    membership = MultiplayerMembership.get_by_ids(user_id, session_id)

    if action == SessionAdminUserAction.KICK:
        add_audit_entry(sio, session,
                        f"Kicked {membership.effective_name}" if membership.user != sio.get_current_user()
                        else "Left session")
        membership.delete_instance()
        if not list(session.players):
            session.delete_instance(recursive=True)
            logger().info(f"{describe_session(session)}. Kicking user {user_id} and deleting session.")
        else:
            logger().info(f"{describe_session(session)}. Kicking user {user_id}.")

    elif action == SessionAdminUserAction.MOVE:
        offset: int = arg
        if membership.is_observer is None:
            raise InvalidAction("Player is an observer")

        new_row = membership.row + offset
        if new_row < 0:
            raise InvalidAction("New position is negative")
        if new_row >= session.num_rows:
            raise InvalidAction("New position is beyond num of rows")

        team_members = [None] * session.num_rows
        for member in MultiplayerMembership.non_observer_members(session):
            team_members[member.row] = member

        while (0 <= new_row < session.num_rows) and team_members[new_row] is not None:
            new_row += offset

        if new_row < 0 or new_row >= session.num_rows:
            raise InvalidAction("No empty slots found in this direction")

        with database.db.atomic():
            logger().info(f"{describe_session(session)}, User {user_id}. "
                          f"Performing {action}, new row is {new_row}, from {membership.row}.")
            membership.row = new_row
            membership.inventory = None
            membership.save()

    elif action == SessionAdminUserAction.ASSOCIATE:
        if membership.is_observer:
            membership.row = _find_empty_row(session)
        else:
            membership.row = None
            membership.inventory = None
        logger().info(f"{describe_session(session)}, User {user_id}. Performing {action}, "
                      f"new row is {membership.row}.")
        membership.save()

    elif action == SessionAdminUserAction.SWITCH_ADMIN:
        # Must be admin for this
        _verify_has_admin(sio, session_id, None, allow_when_no_admins=True)
        num_admins = MultiplayerMembership.select().where(MultiplayerMembership.session == session_id,
                                                          is_boolean(MultiplayerMembership.admin, True)).count()

        if membership.admin and num_admins <= 1:
            raise InvalidAction("can't demote the only admin")

        membership.admin = not membership.admin
        add_audit_entry(sio, session, f"Made {membership.effective_name} {'' if membership.admin else 'not '}an admin")
        logger().info(f"{describe_session(session)}, User {user_id}. Performing {action}, "
                      f"new status is {membership.admin}.")
        membership.save()

    elif action == SessionAdminUserAction.CREATE_PATCHER_FILE:
        player_names = {i: f"Player {i + 1}" for i in range(session.num_rows)}
        uuids = {}

        for member in MultiplayerMembership.non_observer_members(session):
            player_names[member.row] = member.effective_name
            uuids[member.row] = member.effective_name

        layout_description = session.layout_description
        players_config = PlayersConfiguration(
            player_index=membership.row,
            player_names=player_names,
            uuids=uuids,
        )
        preset = layout_description.get_preset(players_config.player_index)
        cosmetic_patches = preset.game.data.layout.cosmetic_patches.from_json(arg)

        add_audit_entry(sio, session, f"Made an ISO for row {membership.row + 1}")

        data_factory = preset.game.patch_data_factory(layout_description, players_config, cosmetic_patches)
        try:
            return data_factory.create_data()
        except Exception as e:
            logger().exception("Error when creating patch data")
            raise InvalidAction(f"Unable to export game: {e}")

    elif action == SessionAdminUserAction.ABANDON:
        # FIXME
        raise InvalidAction("Abandon is NYI")

    emit_session_meta_update(session)


def _query_for_actions(membership: MultiplayerMembership) -> peewee.ModelSelect:
    return WorldAction.select().where(
        WorldAction.provider_row != membership.row,
        WorldAction.session == membership.session,
        WorldAction.receiver_row == membership.row,
    ).order_by(WorldAction.time.asc())


def _base64_encode_pickup(pickup: PickupEntry, resource_database: ResourceDatabase) -> str:
    encoded_pickup = bitpacking.pack_value(BitPackPickupEntry(pickup, resource_database))
    return base64.b85encode(encoded_pickup).decode("utf-8")


def _collect_location(session: MultiplayerSession, membership: MultiplayerMembership,
                      description: LayoutDescription,
                      pickup_location: int) -> int | None:
    """
    Collects the pickup in the given location. Returns
    :param session:
    :param membership:
    :param description:
    :param pickup_location:
    :return: The rewarded player if some player must be updated of the fact.
    """
    player_row: int = membership.row
    pickup_target = _get_pickup_target(description, player_row, pickup_location)

    def log(msg):
        logger().info(f"{describe_session(session, membership)} found item at {pickup_location}. {msg}")

    if pickup_target is None:
        log("It's an ETM.")
        return None

    if pickup_target.player == membership.row:
        log(f"It's a {pickup_target.pickup.name} for themselves.")
        return None

    try:
        WorldAction.create(
            session=session,
            provider_row=membership.row,
            provider_location_index=pickup_location,
            receiver_row=pickup_target.player,
        )
    except peewee.IntegrityError:
        # Already exists and it's for another player, no inventory update needed
        log(f"It's a {pickup_target.pickup.name} for {pickup_target.player}, but it was already collected.")
        return None

    log(f"It's a {pickup_target.pickup.name} for {pickup_target.player}.")
    return pickup_target.player


def _get_resource_database(description: LayoutDescription, player: int) -> ResourceDatabase:
    return default_database.resource_database_for(description.get_preset(player).game)


def _get_pickup_target(description: LayoutDescription, provider: int, location: int) -> PickupTarget | None:
    pickup_assignment = description.all_patches[provider].pickup_assignment
    return pickup_assignment.get(PickupIndex(location))


def emit_game_session_pickups_update(sio: ServerApp, membership: MultiplayerMembership):
    session: MultiplayerSession = membership.session

    if session.state == MultiplayerSessionState.SETUP:
        raise RuntimeError("Unable to emit pickups during SETUP")

    if membership.is_observer:
        raise RuntimeError("Unable to emit pickups for observers")

    description = session.layout_description
    worlds: dict[uuid.UUID, World] = {
        w.uuid: w
        for w in session.worlds
    }
    index_order = [w for w in worlds.keys()]

    resource_database = _get_resource_database(description, membership.row)

    result = []
    actions: list[WorldAction] = list(_query_for_actions(membership))
    for action in actions:
        pickup_target = _get_pickup_target(description, index_order.index(action.provider.uuid),
                                           action.location)

        if pickup_target is None:
            logging.error(f"Action {action} has a location index with nothing.")
            result.append(None)
        else:
            result.append({
                "provider_name": worlds[action.provider].name,
                "pickup": _base64_encode_pickup(pickup_target.pickup, resource_database),
            })

    logger().info(f"{describe_session(session, membership)} "
                  f"notifying {resource_database.game_enum.value} of {len(result)} pickups.")

    data = {
        "game": resource_database.game_enum.value,
        "pickups": result,
    }
    flask_socketio.emit("game_session_pickups_update", data, room=f"game-session-{session.id}-{membership.user.id}")


def setup_app(sio: ServerApp):
    # sio.on("disconnect_game_session", disconnect_game_session)
    sio.on("game_session_admin_session", game_session_admin_session)
    sio.on("game_session_admin_player", game_session_admin_player)
