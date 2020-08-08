from typing import Optional, Union, Dict, List

from PyDrocsid.database import db_thread, db
from PyDrocsid.settings import Settings
from PyDrocsid.translations import translations
from PyDrocsid.util import send_long_embed
from discord import Role, Embed, Member
from discord.ext import commands
from discord.ext.commands import Cog, Bot, CommandError, Context, guild_only, UserInputError

from models.role_auth import RoleAuth
from permissions import PermissionLevel, Permission
from util import send_to_changelog, code


async def configure_role(ctx: Context, role_name: str, role: Role, check_assignable: bool = False):
    if check_assignable:
        if role >= ctx.me.top_role:
            raise CommandError(translations.f_role_not_set_too_high(role, ctx.me.top_role))
        if role.managed:
            raise CommandError(translations.f_role_not_set_managed_role(role))
    await Settings.set(int, role_name + "_role", role.id)
    await ctx.send(translations.role_set)
    await send_to_changelog(
        ctx.guild, translations.f_log_role_set(translations.role_names[role_name], role.name, role.id),
    )


async def is_authorized(author: Member, target_role: Role) -> bool:
    roles = {role.id for role in author.roles} | {author.id}
    return any(auth.source in roles for auth in await db_thread(RoleAuth.all, target=target_role.id))


class RolesCog(Cog, name="Roles"):
    def __init__(self, bot: Bot):
        self.bot = bot

        def set_role(role_name: str):
            async def inner(ctx: Context, *, role: Role):
                await configure_role(ctx, role_name, role, role_name == "mute")

            return inner

        for name, title in translations.role_names.items():
            self.roles_config.command(name=name, help=f"configure {title.lower()} role")(set_role(name))

    @commands.group(aliases=["r"])
    @guild_only()
    async def roles(self, ctx: Context):
        """
        manage roles
        """

        if ctx.invoked_subcommand is None:
            raise UserInputError

    @roles.group(name="config", aliases=["conf", "set", "s"])
    @PermissionLevel.ADMINISTRATOR.check
    async def roles_config(self, ctx: Context):
        """
        configure roles
        """

        if len(ctx.message.content.lstrip(ctx.prefix).split()) > 2:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        embed = Embed(title=translations.roles, color=0x256BE6)
        for name, title in translations.role_names.items():
            role = ctx.guild.get_role(await Settings.get(int, name + "_role"))
            val = role.mention if role is not None else translations.role_not_set
            embed.add_field(name=title, value=val, inline=True)
        await ctx.send(embed=embed)

    @roles.group(name="auth")
    @PermissionLevel.ADMINISTRATOR.check
    async def roles_auth(self, ctx: Context):
        """
        configure role assignment authorizations
        """

        if len(ctx.message.content.lstrip(ctx.prefix).split()) > 2:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        embed = Embed(title=translations.role_auth, colour=0x256BE6)
        members: Dict[Member, List[Role]] = {}
        roles: Dict[Role, List[Role]] = {}
        for auth in await db_thread(RoleAuth.all):  # type: RoleAuth
            source: Optional[Union[Member, Role]] = ctx.guild.get_member(auth.source) or ctx.guild.get_role(auth.source)
            target: Optional[Role] = ctx.guild.get_role(auth.target)
            if source is None or target is None:
                await db_thread(db.delete, auth)
            else:
                [members, roles][isinstance(source, Role)].setdefault(source, []).append(target)
        if not members and not roles:
            embed.description = translations.no_role_auth
            embed.colour = 0xCF0606
            await ctx.send(embed=embed)
            return

        def make_field(auths: Dict[Union[Member, Role], List[Role]]) -> List[str]:
            return [
                f":small_orange_diamond: {src.mention} -> " + ", ".join(t.mention for t in targets)
                for src, targets in sorted(auths.items(), key=lambda a: a[0].name)
            ]

        if roles:
            embed.add_field(name=translations.role_auths, value="\n".join(make_field(roles)), inline=False)
        if members:
            embed.add_field(name=translations.user_auths, value="\n".join(make_field(members)), inline=False)
        await ctx.send(embed=embed)

    @roles_auth.command(name="add", aliases=["a", "+"])
    async def roles_auth_add(self, ctx: Context, source: Union[Member, Role], target: Role):
        """
        add a new role assignment authorization
        """

        if await db_thread(RoleAuth.check, source.id, target.id):
            raise CommandError(translations.role_auth_already_exists)

        await db_thread(RoleAuth.add, source.id, target.id)
        await ctx.send(translations.role_auth_created)
        await send_to_changelog(ctx.guild, translations.f_log_role_auth_created(source, target))

    @roles_auth.command(name="remove", aliases=["r", "del", "d", "-"])
    async def roles_auth_remove(self, ctx: Context, source: Union[Member, Role], target: Role):
        """
        remove a role assignment authorization
        """

        if not await db_thread(RoleAuth.check, source.id, target.id):
            raise CommandError(translations.role_auth_not_found)

        await db_thread(RoleAuth.remove, source.id, target.id)
        await ctx.send(translations.role_auth_removed)
        await send_to_changelog(ctx.guild, translations.f_log_role_auth_removed(source, target))

    @roles.command(name="add", aliases=["a", "+"])
    async def roles_add(self, ctx: Context, member: Member, *, role: Role):
        """
        assign a role to a member
        """

        if not await is_authorized(ctx.author, role):
            raise CommandError(translations.role_not_authorized)

        await member.add_roles(role)
        await ctx.message.add_reaction("\u2705")

    @roles.command(name="remove", aliases=["r", "del", "d", "-"])
    async def roles_remove(self, ctx: Context, member: Member, *, role: Role):
        """
        remove a role from a member
        """

        if not await is_authorized(ctx.author, role):
            raise CommandError(translations.role_not_authorized)

        await member.remove_roles(role)
        await ctx.message.add_reaction("\u2705")

    @roles.command(name="list", aliases=["l", "?"])
    @Permission.list_members.check
    async def roles_list(self, ctx: Context, *, role: Role):
        """
        list all members with a specific role
        """

        out = [translations.f_member_list_line(member.mention, code(f"@{member}")) for member in role.members]
        if out:
            embed = Embed(title=translations.member_list, colour=0x256BE6, description="\n".join(out))
        else:
            embed = Embed(title=translations.member_list, colour=0xCF0606, description=translations.no_members)
        await send_long_embed(ctx, embed)
