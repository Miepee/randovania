import construct
import flask_socketio

from randovania.lib.construct_lib import convert_to_raw_python
from randovania.network_common.binary_formats import BinaryInventory
from randovania.server.database import MultiplayerMembership, MultiplayerSession, MultiplayerAuditEntry
from randovania.server.lib import logger
from randovania.server.server_app import ServerApp


def emit_inventory_update(membership: MultiplayerMembership):
    if membership.inventory is None:
        return

    session_id = membership.session.id
    flask_socketio.emit("game_session_binary_inventory",
                        (session_id, membership.row, membership.inventory),
                        room=f"game-session-{session_id}-binary-inventory",
                        namespace="/")
    try:
        flask_socketio.emit("game_session_json_inventory",
                            (session_id, membership.row,
                             convert_to_raw_python(BinaryInventory.parse(membership.inventory))),
                            room=f"game-session-{session_id}-json-inventory",
                            namespace="/")
    except construct.ConstructError as e:
        logger().warning("Unable to encode inventory for session %d, row %d: %s", session_id, membership.row, str(e))


def describe_session(session: MultiplayerSession, membership: MultiplayerMembership | None = None) -> str:
    if membership is not None:
        return f"Session {session.id} ({session.name}), Row {membership.row} ({membership.effective_name})"
    else:
        return f"Session {session.id} ({session.name})"


def emit_session_meta_update(session: MultiplayerSession):
    logger().debug("game_session_meta_update for session %d (%s)", session.id, session.name)
    flask_socketio.emit(
        "game_session_meta_update",
        session.create_session_entry().as_json,
        room=f"game-session-{session.id}",
        namespace="/",
    )


def emit_session_actions_update(session: MultiplayerSession):
    logger().debug("game_session_actions_update for session %d (%s)", session.id, session.name)
    flask_socketio.emit("game_session_actions_update", session.describe_actions(), room=f"game-session-{session.id}",
                        namespace="/")


def emit_session_audit_update(session: MultiplayerSession):
    logger().debug("game_session_audit_update for session %d (%s)", session.id, session.name)
    flask_socketio.emit("game_session_audit_update", session.get_audit_log(), room=f"game-session-{session.id}",
                        namespace="/")


def add_audit_entry(sio: ServerApp, session: MultiplayerSession, message: str):
    MultiplayerAuditEntry.create(
        session=session,
        user=sio.get_current_user(),
        message=message
    )
    emit_session_audit_update(session)
