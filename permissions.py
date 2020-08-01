from enum import auto
from typing import Union

from PyDrocsid.permission import BasePermission, BasePermissionLevel
from PyDrocsid.settings import Settings
from PyDrocsid.translations import translations
from discord import Member, User
from discord.ext.commands import Converter, Context, BadArgument


class Permission(BasePermission):
    change_prefix = auto()
    admininfo = auto()
    view_own_permissions = auto()
    view_all_permissions = auto()

    warn = auto()
    mute = auto()
    kick = auto()
    ban = auto()
    view_stats = auto()

    init_join_log = auto()
    manage_rr = auto()

    manage_ar = auto()

    send = auto()
    edit = auto()
    delete = auto()

    log_manage = auto()

    scan_messages = auto()
    view_user = auto()
    view_inactive_users = auto()
    set_inactive_duration = auto()

    @property
    def default_permission_level(self) -> "BasePermissionLevel":
        return PermissionLevel.ADMINISTRATOR


class PermissionLevel(BasePermissionLevel):
    PUBLIC, HEAD_ASSISTANT, HEAD, ADMINISTRATOR, OWNER = range(5)

    @classmethod
    async def get_permission_level(cls, member: Union[Member, User]) -> "PermissionLevel":
        if member.id == 370876111992913922:
            return PermissionLevel.OWNER

        if not isinstance(member, Member):
            return PermissionLevel.PUBLIC

        roles = {role.id for role in member.roles}

        async def has_role(role_name):
            return await Settings.get(int, role_name + "_role") in roles

        if member.guild_permissions.administrator or await has_role("admin"):
            return PermissionLevel.ADMINISTRATOR
        if await has_role("head"):
            return PermissionLevel.HEAD
        if await has_role("head_assistant"):
            return PermissionLevel.HEAD_ASSISTANT

        return PermissionLevel.PUBLIC


class PermissionLevelConverter(Converter):
    async def convert(self, ctx: Context, argument: str) -> PermissionLevel:
        if argument.lower() in ("administrator", "admin", "a"):
            return PermissionLevel.ADMINISTRATOR
        if argument.lower() in ("head", "h"):
            return PermissionLevel.HEAD
        if argument.lower() in ("head_assistant", "headassistant", "ha"):
            return PermissionLevel.HEAD_ASSISTANT
        if argument.lower() in ("public", "p"):
            return PermissionLevel.PUBLIC
        raise BadArgument(translations.invalid_permission_level)
