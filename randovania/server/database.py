from __future__ import annotations

import datetime
import enum
import json
import uuid
from typing import Any, Self

import cachetools
import peewee
import sentry_sdk
from sentry_sdk.tracing_utils import record_sql_queries

from randovania.game_description.resources.pickup_index import PickupIndex
from randovania.games.game import RandovaniaGame
from randovania.layout.layout_description import LayoutDescription
from randovania.layout.preset import Preset
from randovania.layout.versioned_preset import VersionedPreset
from randovania.network_client.multiplayer_session import MultiplayerSessionEntry, MultiplayerUser, GameDetails, \
    MultiplayerWorld
from randovania.network_common.binary_formats import BinaryGameSessionActions, \
    BinaryGameSessionAuditLog
from randovania.network_common.session_state import MultiplayerSessionState


class MonitoredDb(peewee.SqliteDatabase):
    def execute_sql(self, sql, params=None, commit=peewee.SENTINEL):
        with record_sql_queries(
                sentry_sdk.Hub.current, self.cursor, sql, params, paramstyle="format", executemany=False
        ):
            return super().execute_sql(sql, params, commit)


db = MonitoredDb(None, pragmas={'foreign_keys': 1})


def is_boolean(field, value: bool):
    return field == value


class BaseModel(peewee.Model):
    class Meta:
        database = db
        legacy_table_names = False


class EnumField(peewee.CharField):
    """
    This class enable an Enum like field for Peewee
    """

    def __init__(self, choices: type[enum.Enum], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.choices = choices
        self.max_length = 255

    def db_value(self, value: Any) -> Any:
        return value.value

    def python_value(self, value: Any) -> Any:
        return self.choices(type(list(self.choices)[0].value)(value))


class User(BaseModel):
    discord_id = peewee.IntegerField(index=True, null=True)
    name = peewee.CharField()
    admin = peewee.BooleanField(default=False)

    @classmethod
    def get_by_id(cls, pk) -> "User":
        return cls.get(cls._meta.primary_key == pk)

    @property
    def as_json(self):
        return {
            "id": self.id,
            "name": self.name,
            "discord_id": self.discord_id,
        }


def _datetime_now():
    return datetime.datetime.now(datetime.timezone.utc)


class UserAccessToken(BaseModel):
    user = peewee.ForeignKeyField(User, backref="access_tokens")
    name = peewee.CharField()
    creation_date = peewee.DateTimeField(default=_datetime_now)
    last_used = peewee.DateTimeField(default=_datetime_now)

    class Meta:
        primary_key = peewee.CompositeKey('user', 'name')


@cachetools.cached(cache=cachetools.TTLCache(maxsize=64, ttl=600))
def _decode_layout_description(s):
    return LayoutDescription.from_json_dict(json.loads(s))


class MultiplayerSession(BaseModel):
    id: int
    name: str = peewee.CharField()
    password: str | None = peewee.CharField(null=True)
    state: MultiplayerSessionState = EnumField(choices=MultiplayerSessionState,
                                               default=MultiplayerSessionState.SETUP)
    layout_description_json: str | None = peewee.TextField(null=True)
    seed_hash: str | None = peewee.CharField(null=True)
    creator: User = peewee.ForeignKeyField(User)
    creation_date = peewee.DateTimeField(default=_datetime_now)
    generation_in_progress: User | None = peewee.ForeignKeyField(User, null=True)
    dev_features: str | None = peewee.CharField(null=True)

    members: list[MultiplayerMembership]
    worlds: list[World]
    audit_log: list[MultiplayerAuditEntry]

    @classmethod
    def get_by_id(cls, pk) -> Self:
        return cls.get(cls._meta.primary_key == pk)

    @property
    def all_presets(self) -> list[Preset]:
        return [
            VersionedPreset(json.loads(preset.preset)).get_preset()
            for preset in sorted(self.presets, key=lambda it: it.row)
        ]

    @property
    def num_rows(self) -> int:
        return len(self.presets)

    @property
    def layout_description(self) -> LayoutDescription | None:
        # FIXME: a server can have an invalid layout description. Likely from an old version!
        return _decode_layout_description(self.layout_description_json) if self.layout_description_json else None

    @layout_description.setter
    def layout_description(self, description: LayoutDescription | None):
        if description is not None:
            self.layout_description_json = json.dumps(description.as_json(force_spoiler=True))
        else:
            self.layout_description_json = None

    @property
    def creation_datetime(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.creation_date)

    def create_list_entry(self):
        return {
            "id": self.id,
            "name": self.name,
            "has_password": self.password is not None,
            "state": self.state.value,
            "num_players": len(self.players),
            "creator": self.creator.name,
            "creation_date": self.creation_datetime.astimezone(datetime.timezone.utc).isoformat(),
        }

    @property
    def allowed_games(self) -> list[RandovaniaGame]:
        dev_features = self.dev_features or ""
        return [
            game for game in RandovaniaGame.sorted_all_games()
            if game.data.defaults_available_in_game_sessions or game.value in dev_features
        ]

    def describe_actions(self):
        description = self.layout_description
        location_to_name = {
            row: f"Player {row + 1}" for row in range(self.num_rows)
        }
        for membership in self.players:
            if not membership.is_observer:
                location_to_name[membership.row] = membership.effective_name

        def _describe_action(action: WorldAction) -> dict:
            provider = action.provider
            location_index = PickupIndex(action.location)
            time = datetime.datetime.fromisoformat(action.time)
            target = description.all_patches[provider].pickup_assignment[location_index]

            return {
                "provider": action.provider.name,
                "receiver": action.receiver.name,
                "pickup": target.pickup.name,
                "location": action.location,
                "time": time.astimezone(datetime.timezone.utc).isoformat(),
            }

        return BinaryGameSessionActions.build([
            _describe_action(action)
            for action in WorldAction.select().where(WorldAction.session == self
                                                     ).order_by(WorldAction.time.asc())
        ])

    def create_session_entry(self) -> MultiplayerSessionEntry:
        description = self.layout_description

        game_details = None
        if description is not None:
            game_details = GameDetails(
                spoiler=description.has_spoiler,
                word_hash=description.shareable_word_hash,
                seed_hash=description.shareable_hash,
            )

        return MultiplayerSessionEntry(
            id=self.id,
            name=self.name,
            state=self.state,
            users=[
                MultiplayerUser(
                    id=member.user.id,
                    name=member.user.name,
                    admin=member.admin,
                    worlds={},
                )
                for member in self.members
            ],
            worlds=[
                MultiplayerWorld(
                    id=world.uuid,
                    name=world.name,
                    preset=world.preset,
                )
                for world in self.worlds
            ],
            game_details=game_details,
            generation_in_progress=(self.generation_in_progress.id
                                    if self.generation_in_progress is not None else None),
            allowed_games=[],
        )

    def get_audit_log(self):
        return BinaryGameSessionAuditLog.build([
            entry.as_json
            for entry in self.audit_log
        ])

    def reset_layout_description(self):
        self.layout_description_json = None
        self.save()


class World(BaseModel):
    id: int
    session: MultiplayerSession = peewee.ForeignKeyField(MultiplayerSession, backref="worlds")
    uuid: uuid.UUID = peewee.UUIDField(default=uuid.uuid4, unique=True)

    name: str = peewee.CharField()
    preset: str = peewee.TextField()
    order: int | None = peewee.IntegerField(null=True, default=None)

    @classmethod
    def get_by_uuid(cls, uid) -> World:
        return cls.get(World.uuid == uid)


class WorldUserAssociation(BaseModel):
    """A given user's association to one given row."""
    world: World = peewee.ForeignKeyField(World)
    user: User = peewee.ForeignKeyField(User)

    connection_state = peewee.TextField(null=True)
    inventory = peewee.BlobField(null=True)

    @classmethod
    def get_by_ids(cls, world_uid: uuid.UUID, user_id: int) -> Self:
        return cls.get(
            WorldUserAssociation.world.uuid == world_uid,
            WorldUserAssociation.user == user_id,
        )

    class Meta:
        primary_key = peewee.CompositeKey('world', 'user')


class MultiplayerMembership(BaseModel):
    user = peewee.ForeignKeyField(User, backref="sessions")
    session = peewee.ForeignKeyField(MultiplayerSession, backref="members")
    admin: bool = peewee.BooleanField()
    join_date = peewee.DateTimeField(default=_datetime_now)

    @property
    def effective_name(self) -> str:
        return self.user.name

    @classmethod
    def get_by_ids(cls, user_id: int, session_id: int) -> Self:
        return cls.get(
            MultiplayerMembership.session == session_id,
            MultiplayerMembership.user == user_id,
        )

    class Meta:
        primary_key = peewee.CompositeKey('user', 'session')


class WorldAction(BaseModel):
    provider: World = peewee.ForeignKeyField(World, backref="actions")
    location: int = peewee.IntegerField()

    session: MultiplayerSession = peewee.ForeignKeyField(MultiplayerSession)
    receiver: World = peewee.ForeignKeyField(World)
    time: str = peewee.DateTimeField(default=_datetime_now)

    class Meta:
        primary_key = peewee.CompositeKey('provider', 'location')


class MultiplayerAuditEntry(BaseModel):
    session: MultiplayerSession = peewee.ForeignKeyField(MultiplayerSession, backref="audit_log")
    user: User = peewee.ForeignKeyField(User)
    message: str = peewee.TextField()
    time: str = peewee.DateTimeField(default=_datetime_now)

    @property
    def as_json(self):
        time = datetime.datetime.fromisoformat(self.time)

        return {
            "user": self.user.name,
            "message": self.message,
            "time": time.astimezone(datetime.timezone.utc).isoformat(),
        }


all_classes = [
    User, UserAccessToken, MultiplayerSession, World,
    WorldUserAssociation, MultiplayerMembership,
    WorldAction, MultiplayerAuditEntry,
]
