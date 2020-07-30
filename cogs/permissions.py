from typing import Optional

from PyDrocsid.settings import Settings
from PyDrocsid.translations import translations
from PyDrocsid.util import send_long_embed
from discord import Embed, Role
from discord.ext import commands
from discord.ext.commands import Cog, Bot, guild_only, Context, CommandError, UserInputError

from permissions import Permission, PermissionLevel, PermissionLevelConverter


async def list_permissions(ctx: Context, title: str, min_level: PermissionLevel):
    out = {}
    for permission in Permission:  # type: Permission
        level = await permission.resolve()
        if min_level.value >= level.value:
            out.setdefault(level.value, []).append(f"`{permission.name}` - {permission.description}")

    embed = Embed(title=title, colour=0xCF0606)
    if not out:
        embed.description = translations.no_permissions
        await ctx.send(embed=embed)
        return

    embed.colour = 0x256BE6
    for level, lines in sorted(out.items(), reverse=True):
        embed.add_field(name=translations.permission_levels[level], value="\n".join(sorted(lines)), inline=False)

    await send_long_embed(ctx, embed)


async def configure_role(ctx: Context, role_name: str, role: Role, check_assignable: bool = False):
    if check_assignable:
        if role >= ctx.me.top_role:
            raise CommandError(translations.f_role_not_set_too_high(role, ctx.me.top_role))
        if role.managed:
            raise CommandError(translations.f_role_not_set_managed_role(role))
    await Settings.set(int, role_name + "_role", role.id)
    await ctx.send(translations.role_set)
    # await send_to_changelog(ctx.guild, getattr(translations, "f_log_role_set_" + role_name)(role.name, role.id))


class PermissionsCog(Cog, name="Permissions"):
    def __init__(self, bot: Bot):
        self.bot = bot

    @commands.group()
    @PermissionLevel.ADMINISTRATOR.check
    @guild_only()
    async def roles(self, ctx: Context):
        """
        configure roles
        """

        if ctx.subcommand_passed is not None:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        embed = Embed(title=translations.roles, color=0x256BE6)
        for role_name in ["admin", "head", "head_assistant"]:
            role = ctx.guild.get_role(await Settings.get(int, role_name + "_role"))
            val = role.mention if role is not None else translations.role_not_set
            embed.add_field(name=getattr(translations, f"role_{role_name}"), value=val, inline=False)
        await ctx.send(embed=embed)

    @roles.command(name="administrator", aliases=["admin", "a"])
    async def roles_administrator(self, ctx: Context, *, role: Role):
        """
        set administrator role
        """

        await configure_role(ctx, "admin", role)

    @roles.command(name="head", aliases=["h"])
    async def roles_head(self, ctx: Context, *, role: Role):
        """
        set administrator role
        """

        await configure_role(ctx, "head", role)

    @roles.command(name="head_assistant", aliases=["ha"])
    async def roles_head_assistant(self, ctx: Context, *, role: Role):
        """
        set administrator role
        """

        await configure_role(ctx, "head_assistant", role)

    @commands.group(aliases=["perm", "p"])
    @guild_only()
    async def permissions(self, ctx: Context):
        """
        manage bot permissions
        """

        if ctx.invoked_subcommand is None:
            raise UserInputError

    @permissions.command(name="list", aliases=["show", "l", "?"])
    @Permission.view_all_permissions.check
    async def permissions_list(self, ctx: Context, min_level: Optional[PermissionLevelConverter]):
        """
        list all permissions
        """

        if min_level is None:
            min_level = PermissionLevel.ADMINISTRATOR

        await list_permissions(ctx, translations.permissions_title, min_level)

    @permissions.command(name="my", aliases=["m", "own", "o"])
    @Permission.view_own_permissions.check
    async def permissions_my(self, ctx: Context):
        """
        list all permissions granted to the user
        """

        min_level: PermissionLevel = await PermissionLevel.get_permission_level(ctx.author)
        await list_permissions(ctx, translations.my_permissions_title, min_level)

    @permissions.command(name="set", aliases=["s", "="])
    @PermissionLevel.ADMINISTRATOR.check
    async def permissions_set(self, ctx: Context, permission: str, level: PermissionLevelConverter):
        """
        configure bot permissions
        """

        level: PermissionLevel
        try:
            permission: Permission = Permission[permission.lower()]
        except KeyError:
            raise CommandError(translations.invalid_permission)

        await permission.set(level)
        await ctx.send(translations.f_permission_set(permission.name, translations.permission_levels[level.value]))
