import json
import uuid

import peewee

import randovania
from randovania.interface_common.players_configuration import PlayersConfiguration
from randovania.layout.layout_description import LayoutDescription
from randovania.layout.versioned_preset import VersionedPreset
from randovania.network_common.admin_actions import SessionAdminGlobalAction, SessionAdminUserAction
from randovania.network_common.error import NotAuthorizedForAction, InvalidAction
from randovania.network_common.session_state import MultiplayerSessionState
from randovania.server import database
from randovania.server.database import MultiplayerMembership, is_boolean, MultiplayerSession, World, \
    WorldUserAssociation, MultiplayerAuditEntry
from randovania.server.lib import logger
from randovania.server.multiplayer import session_common
from randovania.server.multiplayer.session_common import describe_session, get_ordered_worlds, add_audit_entry, \
    emit_session_meta_update
from randovania.server.server_app import ServerApp


def verify_has_admin(sio: ServerApp, session_id: int, admin_user_id: int | None,
                     *, allow_when_no_admins: bool = False) -> None:
    """
    Checks if the logged user can do admin operations to the given session,
    :param sio:
    :param session_id: The GameSessions id.
    :param admin_user_id: An user id that is exceptionally authorized for this.
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


def _create_world(sio: ServerApp, session: MultiplayerSession, arg: tuple[str, dict], for_user: int | None = None):
    if len(arg) != 2:
        raise InvalidAction("Missing arguments.")
    verify_has_admin(sio, session.id, for_user)

    _verify_in_setup(session)
    _verify_no_layout_description(session)
    _verify_not_in_generation(session)
    name, preset_json = arg
    preset = _get_preset(preset_json)

    logger().info(f"{describe_session(session)}: Creating world {name}.")

    world = World.create(session=session, name=name,
                         preset=json.dumps(preset.as_json))
    add_audit_entry(sio, session, f"Created new world {world.name}")
    return world


def _change_world(sio: ServerApp, session: MultiplayerSession, arg: tuple[uuid.UUID, dict]):
    if len(arg) != 2:
        raise InvalidAction("Missing arguments.")
    world_uid, preset_json = arg
    verify_has_admin(sio, session.id, sio.get_current_user().id)
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
            add_audit_entry(sio, session, f"Changing world {world_uid}")

    except peewee.DoesNotExist:
        raise InvalidAction(f"invalid world: {world_uid}")


def _delete_world(sio: ServerApp, session: MultiplayerSession, world_uid: uuid.UUID):
    verify_has_admin(sio, session.id, None)
    _verify_in_setup(session)
    _verify_no_layout_description(session)
    _verify_not_in_generation(session)

    world = World.get_by_uuid(world_uid)
    with database.db.atomic():
        logger().info(f"{describe_session(session)}: Deleting {world.name} ({world_uid}).")
        add_audit_entry(sio, session, f"Deleting world {world.name}")
        WorldUserAssociation.delete().where(WorldUserAssociation.world == world.id).execute()
        world.delete_instance()


def _update_layout_generation(sio: ServerApp, session: MultiplayerSession, world_order: list[int]):
    verify_has_admin(sio, session.id, None)
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


def _change_layout_description(sio: ServerApp, session: MultiplayerSession, description_json: dict | None):
    verify_has_admin(sio, session.id, None)
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
        worlds = get_ordered_worlds(session)

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
    verify_has_admin(sio, session.id, None)
    _verify_in_setup(session)
    if session.layout_description_json is None:
        raise InvalidAction("Unable to start session, no game is available.")

    session.state = MultiplayerSessionState.IN_PROGRESS
    logger().info(f"{describe_session(session)}: Starting session.")
    session.save()
    add_audit_entry(sio, session, "Started session")


def _finish_session(sio: ServerApp, session: MultiplayerSession):
    verify_has_admin(sio, session.id, None)
    if session.state != MultiplayerSessionState.IN_PROGRESS:
        raise InvalidAction("Session is not in progress")

    session.state = MultiplayerSessionState.FINISHED
    logger().info(f"{describe_session(session)}: Finishing session.")
    session.save()
    add_audit_entry(sio, session, "Finished session")


def _change_password(sio: ServerApp, session: MultiplayerSession, password: str):
    verify_has_admin(sio, session.id, None)

    session.password = session_common.hash_password(password)
    logger().info(f"{describe_session(session)}: Changing password.")
    session.save()
    add_audit_entry(sio, session, "Changed password")


def _change_title(sio: ServerApp, session: MultiplayerSession, title: str):
    verify_has_admin(sio, session.id, None)

    old_name = session.name
    session.name = title
    logger().info(f"{describe_session(session)}: Changed name from {old_name}.")
    session.save()
    add_audit_entry(sio, session, f"Changed name from {old_name} to {title}")


def _duplicate_session(sio: ServerApp, session: MultiplayerSession, new_title: str):
    verify_has_admin(sio, session.id, None)

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
        for world in session.worlds:
            assert isinstance(world, World)
            World.create(
                session=new_session,
                name=world.name,
                preset=world.preset,
                order=world.order,
            )
        MultiplayerMembership.create(
            user=current_user,
            session=new_session,
            admin=True,
        )
        MultiplayerAuditEntry.create(
            session=new_session,
            user=current_user,
            message=f"Duplicated from {session.name}",
        )


def _get_permalink(sio: ServerApp, session: MultiplayerSession) -> str:
    verify_has_admin(sio, session.id, None)
    add_audit_entry(sio, session, "Requested permalink")

    return session.layout_description.permalink.as_base64_str


def admin_session(sio: ServerApp, session_id: int, action: str, arg):
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


def _kick_user(sio: ServerApp, session: MultiplayerSession, membership: MultiplayerMembership, user_id: int):
    add_audit_entry(sio, session,
                    f"Kicked {membership.effective_name}" if membership.user != sio.get_current_user()
                    else "Left session")

    with database.db.atomic():
        WorldUserAssociation.delete().where(
            WorldUserAssociation.world.session == session.id,
            WorldUserAssociation.user == membership.user.id,
        ).execute()
        membership.delete_instance()
        if not list(session.players):
            session.delete_instance(recursive=True)
            logger().info(f"{describe_session(session)}. Kicking user {user_id} and deleting session.")
        else:
            logger().info(f"{describe_session(session)}. Kicking user {user_id}.")


def _create_world_for(sio: ServerApp, session: MultiplayerSession, membership: MultiplayerMembership,
                      arg: tuple[str, dict]):
    with database.db.atomic():
        new_world = _create_world(sio, session, arg, membership.user.id)
        WorldUserAssociation.create(
            world=new_world,
            user=membership.user,
        )
        add_audit_entry(sio, session, f"Associated new world {new_world.name} for {membership.user.name}")


def _claim_world(sio: ServerApp, session: MultiplayerSession, user_id: int, world_uid: uuid.UUID):
    if not session.allow_everyone_claim_world:
        verify_has_admin(sio, session.id, None)

    world = World.get_by_uuid(world_uid)

    if not session.allow_coop:
        for _ in WorldUserAssociation.select().where(WorldUserAssociation.world == world.id):
            raise InvalidAction("World is already claimed")

    WorldUserAssociation.create(
        world=world,
        user=user_id,
    )


def _unclaim_world(sio: ServerApp, session: MultiplayerSession, user_id: int, world_uid: uuid.UUID):
    if not session.allow_everyone_claim_world:
        verify_has_admin(sio, session.id, None)

    WorldUserAssociation.get_by_ids(
        world_uid, user_id
    ).delete_instance()


def _switch_admin(sio: ServerApp, session: MultiplayerSession, membership: MultiplayerMembership):
    session_id = session.id

    # Must be admin for this
    verify_has_admin(sio, session_id, None, allow_when_no_admins=True)
    num_admins = MultiplayerMembership.select().where(MultiplayerMembership.session == session_id,
                                                      is_boolean(MultiplayerMembership.admin, True)).count()

    if membership.admin and num_admins <= 1:
        raise InvalidAction("can't demote the only admin")

    membership.admin = not membership.admin
    add_audit_entry(sio, session, f"Made {membership.effective_name} {'' if membership.admin else 'not '}an admin")
    logger().info(f"{describe_session(session)}, User {membership.user.id}. Performing admin switch, "
                  f"new status is {membership.admin}.")
    membership.save()


def _create_patcher_file(sio: ServerApp, session: MultiplayerSession, world_uid: uuid.UUID, cosmetic_json: dict):
    player_names = {}
    uuids = {}
    player_index = None

    for world in session_common.get_ordered_worlds(session):
        player_names[world.order] = world.name
        uuids[world.order] = world.uuid
        if world.uuid == world_uid:
            player_index = world.order

    if player_index is None:
        raise InvalidAction("Unknown world uid for exporting")

    layout_description = session.layout_description
    players_config = PlayersConfiguration(
        player_index=player_index,
        player_names=player_names,
        uuids=uuids,
    )
    preset = layout_description.get_preset(players_config.player_index)
    cosmetic_patches = preset.game.data.layout.cosmetic_patches.from_json(cosmetic_json)

    add_audit_entry(sio, session,
                    f"Exporting game named {players_config.player_names[players_config.player_index]}")

    data_factory = preset.game.patch_data_factory(layout_description, players_config, cosmetic_patches)
    try:
        return data_factory.create_data()
    except Exception as e:
        logger().exception("Error when creating patch data")
        raise InvalidAction(f"Unable to export game: {e}")


def admin_player(sio: ServerApp, session_id: int, user_id: int, action: str, arg):
    verify_has_admin(sio, session_id, user_id)
    action: SessionAdminUserAction = SessionAdminUserAction(action)

    session: MultiplayerSession = database.MultiplayerSession.get_by_id(session_id)
    membership = MultiplayerMembership.get_by_ids(user_id, session_id)

    if action == SessionAdminUserAction.KICK:
        _kick_user(sio, session, membership, user_id)

    elif action == SessionAdminUserAction.CREATE_WORLD_FOR:
        _create_world_for(sio, session, membership, arg)

    elif action == SessionAdminUserAction.CLAIM:
        _claim_world(sio, session, user_id, arg)

    elif action == SessionAdminUserAction.UNCLAIM:
        _unclaim_world(sio, session, user_id, arg)

    elif action == SessionAdminUserAction.SWITCH_ADMIN:
        _switch_admin(sio, session, membership)

    elif action == SessionAdminUserAction.CREATE_PATCHER_FILE:
        world_uid, cosmetic_json = arg
        return _create_patcher_file(sio, session, world_uid, cosmetic_json)

    elif action == SessionAdminUserAction.ABANDON:
        # FIXME
        raise InvalidAction("Abandon is NYI")

    emit_session_meta_update(session)


def setup_app(sio: ServerApp):
    sio.on("multiplayer_admin_session", admin_session)
    sio.on("multiplayer_admin_player", admin_player)
