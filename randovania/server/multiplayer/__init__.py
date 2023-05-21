from randovania.server.multiplayer import session_api
from randovania.server.server_app import ServerApp


def setup_app(sio: ServerApp):
    session_api.setup_app(sio)
