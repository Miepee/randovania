from enum import Enum


class SessionAdminGlobalAction(Enum):
    """Actions that operate on the session itself"""
    CREATE_WORLD = "create_world"
    CHANGE_WORLD = "change_world"
    DELETE_WORLD = "delete_world"
    UPDATE_LAYOUT_GENERATION = "update_layout_generation"
    CHANGE_LAYOUT_DESCRIPTION = "change_layout_description"
    DOWNLOAD_LAYOUT_DESCRIPTION = "download_layout_description"
    START_SESSION = "start_session"
    FINISH_SESSION = "finish_session"
    RESET_SESSION = "reset_session"
    CHANGE_PASSWORD = "change_password"
    CHANGE_TITLE = "change_title"
    DUPLICATE_SESSION = "duplicate_session"
    DELETE_SESSION = "delete_session"
    REQUEST_PERMALINK = "request_permalink"


class SessionAdminUserAction(Enum):
    """Actions that operate on top of a user"""
    KICK = "kick"
    CLAIM = "claim"
    UNCLAIM = "unclaim"
    SWITCH_ADMIN = "switch_admin"
    CREATE_PATCHER_FILE = "create_patcher_file"
    ABANDON = "abandon"
