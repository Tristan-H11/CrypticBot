import re
from datetime import datetime, timedelta
from typing import Optional, Union, List, Tuple

from discord import Role, Guild, Member, Forbidden, HTTPException, User, Embed, NotFound
from discord.ext import commands, tasks
from discord.ext.commands import Cog, Bot, guild_only, Context, CommandError, Converter, BadArgument, UserInputError
from discord.utils import snowflake_time

from PyDrocsid.database import db_thread, db
from PyDrocsid.settings import Settings
from PyDrocsid.translations import translations
from PyDrocsid.util import send_long_embed
from models.mod import Join, Mute, Ban, Leave, UsernameUpdate, Report, Warn, Kick
from permissions import Permission
from util import send_to_changelog, get_prefix, is_teamler, code, codeblock


class DurationConverter(Converter):
    async def convert(self, ctx, argument: str) -> Optional[int]:
        if argument.lower() in ("inf", "perm", "permanent", "-1", "∞"):
            return None
        if (match := re.match(r"^(\d+)d?$", argument)) is None:
            raise BadArgument(translations.invalid_duration)
        if (days := int(match.group(1))) <= 0:
            raise BadArgument(translations.invalid_duration)
        if days >= (1 << 31):
            raise BadArgument(translations.invalid_duration_inf)
        return days


async def get_mute_role(guild: Guild) -> Role:
    mute_role: Optional[Role] = guild.get_role(await Settings.get(int, "mute_role"))
    if mute_role is None:
        raise CommandError(translations.mute_role_not_set)
    return mute_role


async def update_join_date(guild: Guild, user_id: int):
    member: Optional[Member] = guild.get_member(user_id)
    if member is not None:
        await db_thread(Join.update, member.id, str(member), member.joined_at)


class ModCog(Cog, name="Mod Tools"):
    def __init__(self, bot: Bot):
        self.bot = bot

    async def on_ready(self):
        guild: Guild = self.bot.guilds[0]
        mute_role: Optional[Role] = guild.get_role(await Settings.get(int, "mute_role"))
        if mute_role is not None:
            for mute in await db_thread(db.query, Mute, active=True):
                member: Optional[Member] = guild.get_member(mute.member)
                if member is not None:
                    await member.add_roles(mute_role)

        try:
            self.mod_loop.start()
        except RuntimeError:
            self.mod_loop.restart()

    @tasks.loop(minutes=30)
    async def mod_loop(self):
        guild: Guild = self.bot.guilds[0]

        for ban in await db_thread(db.query, Ban, active=True):
            if ban.days != -1 and datetime.utcnow() >= ban.timestamp + timedelta(days=ban.days):
                user: Optional[User] = await self.bot.fetch_user(ban.member)
                if user is not None:
                    await guild.unban(user)
                await send_to_changelog(
                    guild, translations.f_log_unbanned_expired(f"<@{ban.member}>", code(ban.member_name))
                )
                await db_thread(Ban.deactivate, ban.id)

        mute_role: Optional[Role] = guild.get_role(await Settings.get(int, "mute_role"))
        if mute_role is None:
            return

        for mute in await db_thread(db.query, Mute, active=True):
            if mute.days != -1 and datetime.utcnow() >= mute.timestamp + timedelta(days=mute.days):
                member: Optional[Member] = guild.get_member(mute.member)
                if member is not None:
                    await member.remove_roles(mute_role)
                await send_to_changelog(
                    guild, translations.f_log_unmuted_expired(f"<@{mute.member}>", code(mute.member_name))
                )
                await db_thread(Mute.deactivate, mute.id)

    async def on_member_join(self, member: Member):
        await db_thread(Join.create, member.id, str(member))
        mute_role: Optional[Role] = member.guild.get_role(await Settings.get(int, "mute_role"))
        if mute_role is None:
            return

        if await db_thread(db.first, Mute, active=True, member=member.id) is not None:
            await member.add_roles(mute_role)

    async def on_member_remove(self, member: Member):
        await db_thread(Leave.create, member.id, str(member))

    async def on_member_nick_update(self, before: Member, after: Member):
        await db_thread(UsernameUpdate.create, before.id, before.nick, after.nick, True)

    async def on_user_update(self, before: User, after: User):
        if str(before) == str(after):
            return

        await db_thread(UsernameUpdate.create, before.id, str(before), str(after), False)

    @commands.command()
    @guild_only()
    async def report(self, ctx: Context, member: Member, *, reason: str):
        """
        report a member
        """

        if len(reason) > 900:
            raise CommandError(translations.reason_too_long)

        await db_thread(Report.create, member.id, str(member), ctx.author.id, reason)
        await ctx.send(translations.reported_response)
        await send_to_changelog(
            ctx.guild, translations.f_log_reported(ctx.author.mention, member.mention, code(member), code(reason))
        )

    @commands.command()
    @Permission.warn.check
    @guild_only()
    async def warn(self, ctx: Context, member: Member, *, reason: str):
        """
        warn a member
        """

        if len(reason) > 900:
            raise CommandError(translations.reason_too_long)

        if member == self.bot.user:
            raise CommandError(translations.cannot_warn)

        try:
            await member.send(translations.f_warned(ctx.author.mention, ctx.guild.name, codeblock(reason)))
        except (Forbidden, HTTPException):
            await ctx.send(translations.no_dm)
        await db_thread(Warn.create, member.id, str(member), ctx.author.id, reason)
        await ctx.send(translations.warned_response)
        await send_to_changelog(
            ctx.guild, translations.f_log_warned(ctx.author.mention, member.mention, code(member), code(reason))
        )

    async def get_user(self, guild: Guild, user: Union[Member, User, int]) -> Union[Member, User]:
        if isinstance(user, int):
            if (user := guild.get_member(user) or await self.bot.fetch_user(user)) is None:
                raise CommandError(translations.user_not_found)
        elif isinstance(user, User):
            user = guild.get_member(user.id) or user
        return user

    @commands.command()
    @Permission.mute.check
    @guild_only()
    async def mute(self, ctx: Context, user: Union[Member, User, int], days: DurationConverter, *, reason: str):
        """
        mute a member
        set days to inf for a permanent mute
        """

        days: Optional[int]

        if len(reason) > 900:
            raise CommandError(translations.reason_too_long)

        mute_role: Role = await get_mute_role(ctx.guild)
        user: Union[Member, User] = await self.get_user(ctx.guild, user)

        if user == self.bot.user or await is_teamler(user):
            raise CommandError(translations.cannot_mute)

        if await db_thread(db.first, Mute, active=True, member=user.id) is not None:
            raise CommandError(translations.already_muted)
        if isinstance(user, Member):
            if mute_role in user.roles:
                raise CommandError(translations.already_muted)
            await user.add_roles(mute_role)

        try:
            if days is not None:
                await user.send(translations.f_muted(ctx.author.mention, ctx.guild.name, days, codeblock(reason)))
            else:
                await user.send(translations.f_muted_inf(ctx.author.mention, ctx.guild.name, codeblock(reason)))
        except (Forbidden, HTTPException):
            await ctx.send(translations.no_dm)
        if days is not None:
            await db_thread(Mute.create, user.id, str(user), ctx.author.id, days, reason)
            await ctx.send(translations.muted_response)
            await send_to_changelog(
                ctx.guild, translations.f_log_muted(ctx.author.mention, user.mention, code(user), days, code(reason))
            )
        else:
            await db_thread(Mute.create, user.id, str(user), ctx.author.id, -1, reason)
            await ctx.send(translations.muted_response)
            await send_to_changelog(
                ctx.guild, translations.f_log_muted_inf(ctx.author.mention, user.mention, code(user), code(reason))
            )

    @commands.command()
    @Permission.mute.check
    @guild_only()
    async def unmute(self, ctx: Context, user: Union[Member, User, int], *, reason: str):
        """
        unmute a member
        """

        if len(reason) > 900:
            raise CommandError(translations.reason_too_long)

        mute_role: Role = await get_mute_role(ctx.guild)
        user: Union[Member, User] = await self.get_user(ctx.guild, user)

        was_muted = False
        if isinstance(user, Member) and mute_role in user.roles:
            was_muted = True
            await user.remove_roles(mute_role)

        for mute in await db_thread(db.query, Mute, active=True, member=user.id):
            await db_thread(Mute.deactivate, mute.id, ctx.author.id, reason)
            was_muted = True
        if not was_muted:
            raise CommandError(translations.not_muted)

        await ctx.send(translations.unmuted_response)
        await send_to_changelog(
            ctx.guild, translations.f_log_unmuted(ctx.author.mention, user.mention, code(user), code(reason))
        )

    @commands.command()
    @Permission.kick.check
    @guild_only()
    async def kick(self, ctx: Context, member: Member, *, reason: str):
        """
        kick a member
        """

        if len(reason) > 900:
            raise CommandError(translations.reason_too_long)

        if member == self.bot.user or await is_teamler(member):
            raise CommandError(translations.cannot_kick)

        if not ctx.guild.me.guild_permissions.kick_members:
            raise CommandError(translations.cannot_kick_permissions)

        if member.top_role >= ctx.guild.me.top_role or member.id == ctx.guild.owner_id:
            raise CommandError(translations.cannot_kick)

        try:
            await member.send(translations.f_kicked(ctx.author.mention, ctx.guild.name, codeblock(reason)))
        except (Forbidden, HTTPException):
            await ctx.send(translations.no_dm)
        await member.kick(reason=reason)
        await db_thread(Kick.create, member.id, str(member), ctx.author.id, reason)
        await ctx.send(translations.kicked_response)
        await send_to_changelog(
            ctx.guild, translations.f_log_kicked(ctx.author.mention, member.mention, code(member), code(reason))
        )

    @commands.command()
    @Permission.ban.check
    @guild_only()
    async def ban(self, ctx: Context, user: Union[Member, User, int], days: DurationConverter, *, reason: str):
        """
        ban a user
        set days to inf for a permanent ban
        """

        days: Optional[int]

        if not ctx.guild.me.guild_permissions.ban_members:
            raise CommandError(translations.cannot_ban_permissions)

        if len(reason) > 900:
            raise CommandError(translations.reason_too_long)

        user: Union[Member, User] = await self.get_user(ctx.guild, user)

        if user == self.bot.user or await is_teamler(user):
            raise CommandError(translations.cannot_ban)
        if isinstance(user, Member) and (user.top_role >= ctx.guild.me.top_role or user.id == ctx.guild.owner_id):
            raise CommandError(translations.cannot_ban)

        try:
            if days is not None:
                await user.send(translations.f_banned(ctx.author.mention, ctx.guild.name, days, codeblock(reason)))
            else:
                await user.send(translations.f_banned_inf(ctx.author.mention, ctx.guild.name, codeblock(reason)))
        except (Forbidden, HTTPException):
            await ctx.send(translations.no_dm)

        await ctx.guild.ban(user, delete_message_days=1, reason=reason)
        if days is not None:
            await db_thread(Ban.create, user.id, str(user), ctx.author.id, days, reason)
            await ctx.send(translations.banned_response)
            await send_to_changelog(
                ctx.guild, translations.f_log_banned(ctx.author.mention, user.mention, code(user), days, code(reason))
            )
        else:
            await db_thread(Ban.create, user.id, str(user), ctx.author.id, -1, reason)
            await ctx.send(translations.banned_response)
            await send_to_changelog(
                ctx.guild, translations.f_log_banned_inf(ctx.author.mention, user.mention, code(user), code(reason))
            )

    @commands.command()
    @Permission.ban.check
    @guild_only()
    async def unban(self, ctx: Context, user: Union[User, int], *, reason: str):
        """
        unban a user
        """

        if len(reason) > 900:
            raise CommandError(translations.reason_too_long)

        if not ctx.guild.me.guild_permissions.ban_members:
            raise CommandError(translations.cannot_unban_permissions)

        user: Union[Member, User] = await self.get_user(ctx.guild, user)

        was_banned = True
        try:
            await ctx.guild.unban(user, reason=reason)
        except HTTPException:
            was_banned = False

        for ban in await db_thread(db.query, Ban, active=True, member=user.id):
            was_banned = True
            await db_thread(Ban.deactivate, ban.id, ctx.author.id, reason)
        if not was_banned:
            raise CommandError(translations.not_banned)

        await ctx.send(translations.unbanned_response)
        await send_to_changelog(
            ctx.guild, translations.f_log_unbanned(ctx.author.mention, user.mention, code(user), code(reason))
        )

    async def get_stats_user(
        self, ctx: Context, user: Optional[Union[User, int]]
    ) -> Tuple[Union[User, int], int, bool]:
        arg_passed = len(ctx.message.content.strip(await get_prefix()).split()) >= 2
        if user is None:
            if arg_passed:
                raise UserInputError
            user = ctx.author

        if isinstance(user, int):
            if not 0 <= user < (1 << 63):
                raise UserInputError
            try:
                user = await self.bot.fetch_user(user)
            except NotFound:
                pass

        user_id = user if isinstance(user, int) else user.id

        if user_id != ctx.author.id and not await Permission.view_stats.check_permissions(ctx.author):
            raise CommandError(translations.stats_not_allowed)

        return user, user_id, arg_passed

    @commands.command()
    async def stats(self, ctx: Context, user: Optional[Union[User, int]] = None):
        """
        show statistics about a user
        """

        user, user_id, arg_passed = await self.get_stats_user(ctx, user)
        await update_join_date(self.bot.guilds[0], user_id)

        embed = Embed(title=translations.stats, color=0x35992C)
        if isinstance(user, int):
            embed.set_author(name=str(user))
        else:
            embed.set_author(name=f"{user} ({user_id})", icon_url=user.avatar_url)

        async def count(cls):
            if cls is Report:
                active = await db_thread(db.count, cls, reporter=user_id)
            else:
                active = await db_thread(db.count, cls, mod=user_id)
            passive = await db_thread(db.count, cls, member=user_id)
            return translations.f_active_passive(active, passive)

        embed.add_field(name=translations.reported_cnt, value=await count(Report))
        embed.add_field(name=translations.warned_cnt, value=await count(Warn))
        embed.add_field(name=translations.muted_cnt, value=await count(Mute))
        embed.add_field(name=translations.kicked_cnt, value=await count(Kick))
        embed.add_field(name=translations.banned_cnt, value=await count(Ban))

        if (ban := await db_thread(db.first, Ban, member=user_id, active=True)) is not None:
            if ban.days != -1:
                expiry_date: datetime = ban.timestamp + timedelta(days=ban.days)
                days_left = (expiry_date - datetime.utcnow()).days + 1
                status = translations.f_status_banned_days(ban.days, days_left)
            else:
                status = translations.status_banned
        elif (mute := await db_thread(db.first, Mute, member=user_id, active=True)) is not None:
            if mute.days != -1:
                expiry_date: datetime = mute.timestamp + timedelta(days=mute.days)
                days_left = (expiry_date - datetime.utcnow()).days + 1
                status = translations.f_status_muted_days(mute.days, days_left)
            else:
                status = translations.status_muted
        elif (member := self.bot.guilds[0].get_member(user_id)) is not None:
            status = translations.f_member_since(member.joined_at.strftime("%d.%m.%Y %H:%M:%S"))
        else:
            status = translations.not_a_member
        embed.add_field(name=translations.status, value=status, inline=False)

        if arg_passed:
            await ctx.send(embed=embed)
        else:
            try:
                await ctx.author.send(embed=embed)
            except (Forbidden, HTTPException):
                raise CommandError(translations.could_not_send_dm)
            await ctx.message.add_reaction("\u2705")

    @commands.command(aliases=["userlog", "ulog", "uinfo", "userinfo"])
    async def userlogs(self, ctx: Context, user: Optional[Union[User, int]] = None):
        """
        show moderation log of a user
        """

        user, user_id, arg_passed = await self.get_stats_user(ctx, user)
        await update_join_date(self.bot.guilds[0], user_id)

        out: List[Tuple[datetime, str]] = [(snowflake_time(user_id), translations.ulog_created)]
        for join in await db_thread(db.query, Join, member=user_id):
            out.append((join.timestamp, translations.ulog_joined))
        for leave in await db_thread(db.query, Leave, member=user_id):
            out.append((leave.timestamp, translations.ulog_left))
        for username_update in await db_thread(db.query, UsernameUpdate, member=user_id):
            if not username_update.nick:
                msg = translations.f_ulog_username_updated(
                    code(username_update.member_name), code(username_update.new_name)
                )
            elif username_update.member_name is None:
                msg = translations.f_ulog_nick_set(code(username_update.new_name))
            elif username_update.new_name is None:
                msg = translations.f_ulog_nick_cleared(code(username_update.member_name))
            else:
                msg = translations.f_ulog_nick_updated(
                    code(username_update.member_name), code(username_update.new_name)
                )
            out.append((username_update.timestamp, msg))
        for report in await db_thread(db.query, Report, member=user_id):
            out.append((report.timestamp, translations.f_ulog_reported(f"<@{report.reporter}>", code(report.reason))))
        for warn in await db_thread(db.query, Warn, member=user_id):
            out.append((warn.timestamp, translations.f_ulog_warned(f"<@{warn.mod}>", code(warn.reason))))
        for mute in await db_thread(db.query, Mute, member=user_id):
            if mute.days == -1:
                out.append((mute.timestamp, translations.f_ulog_muted_inf(f"<@{mute.mod}>", code(mute.reason))))
            else:
                out.append((mute.timestamp, translations.f_ulog_muted(f"<@{mute.mod}>", mute.days, code(mute.reason))))
            if not mute.active:
                if mute.unmute_mod is None:
                    out.append((mute.deactivation_timestamp, translations.ulog_unmuted_expired))
                else:
                    out.append(
                        (
                            mute.deactivation_timestamp,
                            translations.f_ulog_unmuted(f"<@{mute.unmute_mod}>", code(mute.unmute_reason)),
                        )
                    )
        for kick in await db_thread(db.query, Kick, member=user_id):
            if kick.mod is not None:
                out.append((kick.timestamp, translations.f_ulog_kicked(f"<@{kick.mod}>", code(kick.reason))))
            else:
                out.append((kick.timestamp, translations.ulog_autokicked))
        for ban in await db_thread(db.query, Ban, member=user_id):
            if ban.days == -1:
                out.append((ban.timestamp, translations.f_ulog_banned_inf(f"<@{ban.mod}>", code(ban.reason))))
            else:
                out.append((ban.timestamp, translations.f_ulog_banned(f"<@{ban.mod}>", ban.days, code(ban.reason))))
            if not ban.active:
                if ban.unban_mod is None:
                    out.append((ban.deactivation_timestamp, translations.ulog_unbanned_expired))
                else:
                    out.append(
                        (
                            ban.deactivation_timestamp,
                            translations.f_ulog_unbanned(f"<@{ban.unban_mod}>", code(ban.unban_reason)),
                        )
                    )

        out.sort()
        embed = Embed(title=translations.userlogs, color=0x34B77E)
        if isinstance(user, int):
            embed.set_author(name=str(user))
        else:
            embed.set_author(name=f"{user} ({user_id})", icon_url=user.avatar_url)
        for row in out:
            name = row[0].strftime("%d.%m.%Y %H:%M:%S")
            value = row[1]
            embed.add_field(name=name, value=value, inline=False)

        embed.set_footer(text=translations.utc_note)
        if arg_passed:
            await send_long_embed(ctx, embed)
        else:
            try:
                await send_long_embed(ctx.author, embed)
            except (Forbidden, HTTPException):
                raise CommandError(translations.could_not_send_dm)
            await ctx.message.add_reaction("\u2705")

    @commands.command()
    @Permission.init_join_log.check
    @guild_only()
    async def init_join_log(self, ctx: Context):
        """
        create a join log entry for each server member
        """

        guild: Guild = ctx.guild

        def init():
            for member in guild.members:  # type: Member
                Join.update(member.id, str(member), member.joined_at)

        await ctx.send(translations.f_filling_join_log(len(guild.members)))
        await db_thread(init)
        await ctx.send(translations.join_log_filled)
