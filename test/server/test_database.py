import peewee
import pytest
from peewee import SqliteDatabase

from randovania.layout.layout_description import LayoutDescription
from randovania.lib import construct_lib
# from randovania.network_common.binary_formats import BinaryMultiplayerSessionEntry
from randovania.server import database


def test_init(tmpdir):
    test_db = SqliteDatabase(':memory:')
    with test_db.bind_ctx(database.all_classes):
        test_db.connect(reuse_if_open=True)
        test_db.create_tables(database.all_classes)


@pytest.mark.parametrize("has_description", [False, True])
def test_GameSession_create_session_entry(clean_database, has_description, test_files_dir, mocker):
    # Setup
    description = LayoutDescription.from_file(test_files_dir.joinpath("log_files", "seed_a.rdvgame"))
    someone = database.User.create(name="Someone")
    s = database.MultiplayerSession.create(name="Debug", num_teams=1, creator=someone)
    game_details = None
    if has_description:
        s.layout_description = description
        s.save()
        game_details = {
            'seed_hash': 'VNBKJI3X',
            'spoiler': True,
            'word_hash': 'Dead Skiff Suit',
        }

    # Run
    session = database.MultiplayerSession.get_by_id(1)
    result = session.create_session_entry()
    readable_result = construct_lib.convert_to_raw_python(BinaryMultiplayerSessionEntry.parse(result))

    # Assert
    assert readable_result == {
        'allowed_games': ['prime1', 'prime2'],
        'game_details': game_details,
        'generation_in_progress': None,
        'id': 1,
        'name': 'Debug',
        'players': [],
        'presets': [],
        'state': 'setup',
    }


def test_fun(clean_database):
    user1 = database.User.create(name="Someone")
    user2 = database.User.create(name="Other")
    session1 = database.MultiplayerSession.create(name="Debug1", creator=user1)
    session2 = database.MultiplayerSession.create(name="Debug2", creator=user1)
    world1 = database.World.create(session=session1, name="World1", preset="{}")
    world2 = database.World.create(session=session1, name="World2", preset="{}")
    world3 = database.World.create(session=session1, name="World3", preset="{}")
    world4 = database.World.create(session=session2, name="World4", preset="{}")
    a1 = database.WorldUserAssociation.create(world=world1, user=user1, connection_state="A")
    a2 = database.WorldUserAssociation.create(world=world2, user=user1, connection_state="B")
    database.WorldUserAssociation.create(world=world3, user=user2, connection_state="C")
    database.WorldUserAssociation.create(world=world4, user=user1, connection_state="D")

    result = list(
        database.WorldUserAssociation.find_all_for_user_in_session(
            user_id=user1.id, session_id=session1.id,
        )
    )

    assert result == [a1, a2]
