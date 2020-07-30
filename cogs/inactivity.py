from datetime import datetime
from typing import Optional, List

from PyDrocsid.database import db_thread, db
from PyDrocsid.settings import Settings
from PyDrocsid.translations import translations
from PyDrocsid.util import send_long_embed
from discord import Message, Guild, Member, Embed, Role
from discord.ext import commands
from discord.ext.commands import Cog, Bot, guild_only, Context, CommandError

from models.activity import Activity
from permissions import Permission
from util import ACTIVE_ROLES


async def should_be_active(member: Member) -> bool:
    if member.bot:
        return False

    roles = {role.id for role in member.roles}
    for role_name in ACTIVE_ROLES:
        if await Settings.get(int, role_name + "_role") in roles:
            return True
    return False


async def collect_active(guild: Guild) -> List[Member]:
    out = set()
    for role_name in ACTIVE_ROLES:
        role: Role = guild.get_role(await Settings.get(int, role_name + "_role"))
        if role is None:
            continue
        out.update(role.members)
    return [member for member in out if not member.bot]


async def last_activity(member: Member) -> Optional[datetime]:
    activity: Optional[Activity] = await db_thread(db.get, Activity, member.id)
    if activity is None:
        return None
    return activity.last_message


class InactivityCog(Cog, name="Inactivity"):
    def __init__(self, bot: Bot):
        self.bot = bot

    async def on_message(self, message: Message):
        if message.author.bot or message.guild is None:
            return

        await db_thread(Activity.update, message.author.id, message.created_at)

    @commands.command()
    @Permission.scan_messages.check
    @guild_only()
    async def scan(self, ctx: Context, days: int):
        """
        scan all channels for latest message of each user
        """

        if days <= 0:
            raise CommandError(translations.invalid_duration)

        now = datetime.utcnow()
        message: Message = await ctx.send(translations.scanning)
        guild: Guild = ctx.guild
        members = {}
        for i, channel in enumerate(guild.text_channels):
            await message.edit(
                content=translations.f_scanning_channel(channel.mention, i + 1, len(guild.text_channels))
            )
            async for msg in channel.history(limit=None, oldest_first=False):
                if (now - msg.created_at).total_seconds() > days * 24 * 60 * 60:
                    break
                if msg.author.bot:
                    continue
                members[msg.author] = max(members.get(msg.author, msg.created_at), msg.created_at)
        await message.edit(content=translations.f_scan_complete(len(guild.text_channels)))

        message: Message = await ctx.send(translations.updating_members)

        def update_members():
            for member, last_message in members.items():
                Activity.update(member.id, last_message)

        await db_thread(update_members)
        await message.edit(content=translations.f_updated_members(len(members)))

    @commands.command()
    @Permission.view_user.check
    @guild_only()
    async def user(self, ctx: Context, user: Member, inactive_days: Optional[int]):
        """
        view information about a user
        """

        if inactive_days is None:
            inactive_days = await Settings.get(int, "inactive_days", 14)
        elif inactive_days <= 0:
            raise CommandError(translations.invalid_duration)

        embed = Embed(title=translations.user_info, color=0x35992C)
        embed.set_author(name=f"{user} ({user.id})", icon_url=user.avatar_url)

        last_message: Optional[datetime] = await last_activity(user)
        if user.bot:
            status = translations.status_bot
        elif await should_be_active(user):
            if last_message is None:
                status = translations.status_inactive
            elif (days := (datetime.utcnow() - last_message).days) >= inactive_days:
                status = translations.f_status_inactive_since(last_message.strftime("%d.%m.%Y %H:%M:%S"))
            else:
                status = translations.f_status_active(days)
        else:
            status = translations.status_watcher
        embed.add_field(name=translations.status, value=status)

        await ctx.send(embed=embed)

    @commands.command(aliases=["in"])
    @Permission.view_inactive_users.check
    @guild_only()
    async def inactive(self, ctx: Context, days: Optional[int]):
        """
        list inactive users
        """

        if days is None:
            days = await Settings.get(int, "inactive_days", 14)
        elif days <= 0:
            raise CommandError(translations.invalid_duration)

        out = []
        now = datetime.utcnow()
        activity = {a.user_id: a.last_message for a in await db_thread(db.all, Activity)}
        members = []
        for member in await collect_active(ctx.guild):
            last_message: Optional[datetime] = activity.get(member.id)
            if last_message is None:
                members.append((member, None))
            elif (now - last_message).days >= days:
                members.append((member, last_message))
        members.sort(key=lambda a: (1, a[1], str(a[0])) if a[1] else (0, str(a[0])))
        for member, last_message in members:
            if last_message is None:
                out.append(translations.f_user_inactive(member.mention))
            else:
                out.append(
                    translations.f_user_inactive_since(member.mention, last_message.strftime("%d.%m.%Y %H:%M:%S"))
                )

        embed = Embed(title=translations.inactive_users, colour=0x256BE6)
        if out:
            embed.description = "\n".join(out)
        else:
            embed.description = translations.no_inactive_users
            embed.colour = 0x03AD28
        await send_long_embed(ctx, embed)

    @commands.command(aliases=["indur"])
    @Permission.set_inactive_duration.check
    @guild_only()
    async def inactive_duration(self, ctx: Context, days: Optional[int]):
        """
        configure inactivity duration
        """

        if days is None:
            days = await Settings.get(int, "inactive_days", 14)
            await ctx.send(translations.f_inactive_duration(days))
            return
        elif days <= 0:
            raise CommandError(translations.invalid_duration)

        await Settings.set(int, "inactive_days", days)
        await ctx.send(translations.f_inactive_duration_set(days))
