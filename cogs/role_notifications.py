from typing import Optional

from PyDrocsid.database import db_thread, db
from PyDrocsid.translations import translations
from discord import Member, Role, TextChannel, Embed, Guild
from discord.ext import commands
from discord.ext.commands import Cog, Bot, guild_only, Context, UserInputError, CommandError

from models.role_notification import RoleNotification
from permissions import Permission
from util import send_to_changelog


class RoleNotificationsCog(Cog, name="Role Notifications"):
    def __init__(self, bot: Bot):
        self.bot = bot

    async def on_member_role_add(self, member: Member, role: Role):
        for link in await db_thread(db.all, RoleNotification, role_id=role.id):  # type: RoleNotification
            channel: Optional[TextChannel] = self.bot.get_channel(link.channel_id)
            if channel is None:
                continue

            role_name = role.mention if link.ping_role else f"`@{role}`"
            user_name = member.mention if link.ping_user else f"`@{member}`"
            await channel.send(translations.f_rn_role_added(role_name, user_name))

    async def on_member_role_remove(self, member: Member, role: Role):
        for link in await db_thread(db.all, RoleNotification, role_id=role.id):  # type: RoleNotification
            channel: Optional[TextChannel] = self.bot.get_channel(link.channel_id)
            if channel is None:
                continue

            role_name = role.mention if link.ping_role else f"`@{role}`"
            user_name = member.mention if link.ping_user else f"`@{member}`"
            await channel.send(translations.f_rn_role_removed(role_name, user_name))

    @commands.group(aliases=["rn"])
    @Permission.manage_rn.check
    @guild_only()
    async def role_notifications(self, ctx: Context):
        """
        manage role notifications
        """

        if ctx.subcommand_passed is not None:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        embed = Embed(title=translations.rn_links, color=0x256BE6)
        out = []
        guild: Guild = ctx.guild
        for link in await db_thread(db.all, RoleNotification):  # type: RoleNotification
            if guild.get_channel(link.channel_id) is None or guild.get_role(link.role_id) is None:
                await db_thread(db.delete, link)
                continue

            flags = []
            flags += [translations.rn_ping_role] * link.ping_role
            flags += [translations.rn_ping_user] * link.ping_user
            out.append(f"<@&{link.role_id}> -> <#{link.channel_id}>" + (" (" + ", ".join(flags) + ")") * bool(flags))

        if not out:
            embed.description = translations.rn_no_links
            embed.colour = 0xCF0606
        else:
            embed.description = "\n".join(out)
        await ctx.send(embed=embed)

    @role_notifications.command(name="add", aliases=["a", "+"])
    async def role_notifications_add(
        self, ctx: Context, role: Role, channel: TextChannel, ping_role: bool, ping_user: bool
    ):
        """
        add a role notification link
        """

        if await db_thread(db.first, RoleNotification, role_id=role.id, channel_id=channel.id) is not None:
            raise CommandError(translations.link_already_exists)

        await db_thread(RoleNotification.create, role.id, channel.id, ping_role, ping_user)
        await ctx.send(translations.rn_created)
        await send_to_changelog(ctx.guild, translations.f_log_rn_created(role, channel.mention))

    @role_notifications.command(name="remove", aliases=["del", "r", "d", "-"])
    async def role_notifications_remove(self, ctx: Context, role: Role, channel: TextChannel):
        """
        remove a role notification link
        """

        link: Optional[RoleNotification] = await db_thread(
            db.first, RoleNotification, role_id=role.id, channel_id=channel.id
        )
        if link is None:
            raise CommandError(translations.link_not_found)

        name: str = role.name if (role := ctx.guild.get_role(link.role_id)) else "deleted-role"

        await db_thread(db.delete, link)
        await ctx.send(translations.rn_removed)
        await send_to_changelog(ctx.guild, translations.f_log_rn_removed(name, channel.mention))
