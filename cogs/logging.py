from datetime import datetime, timedelta
from typing import Optional, Set

from discord import (
    TextChannel,
    Message,
    Embed,
    RawMessageDeleteEvent,
    Member,
)
from discord.ext import commands, tasks
from discord.ext.commands import Cog, Bot, guild_only, Context, CommandError, UserInputError

from PyDrocsid.database import db_thread
from PyDrocsid.settings import Settings
from PyDrocsid.translations import translations
from PyDrocsid.util import calculate_edit_distance
from models.log_exclude import LogExclude
from permissions import Permission
from util import send_to_changelog

ignored_messages: Set[int] = set()


def ignore(message: Message) -> Message:
    ignored_messages.add(message.id)
    return message


async def delete_nolog(message: Message, delay: Optional[int] = None):
    await ignore(message).delete(delay=delay)


def add_field(embed: Embed, name: str, text: str):
    first = True
    while text:
        embed.add_field(name=["\ufeff", name][first], value=text[:1024], inline=False)
        text = text[1024:]
        first = False


class LoggingCog(Cog, name="Logging"):
    def __init__(self, bot: Bot):
        self.bot = bot

    async def get_logging_channel(self, event: str) -> Optional[TextChannel]:
        return self.bot.get_channel(await Settings.get(int, "logging_" + event, -1))

    async def is_logging_channel(self, channel: TextChannel) -> bool:
        return channel.id in [(await self.get_logging_channel(event)).id for event in ["edit", "delete"]]

    async def on_ready(self):
        try:
            self.cleanup_loop.start()
        except RuntimeError:
            self.cleanup_loop.restart()

    @tasks.loop(minutes=30)
    async def cleanup_loop(self):
        days: int = await Settings.get(int, "logging_maxage", -1)
        if days == -1:
            return

        timestamp = datetime.utcnow() - timedelta(days=days)
        for event in ["edit", "delete"]:
            channel: Optional[TextChannel] = await self.get_logging_channel(event)
            if channel is None:
                continue

            async for message in channel.history(limit=None, oldest_first=True):  # type: Message
                if message.created_at > timestamp:
                    break

                await message.delete()

    async def on_message_edit(self, before: Message, after: Message):
        if before.guild is None:
            return
        if before.id in ignored_messages:
            ignored_messages.remove(before.id)
            return
        mindiff: int = await Settings.get(int, "logging_edit_mindiff", 1)
        if calculate_edit_distance(before.content, after.content) < mindiff:
            return
        if (edit_channel := await self.get_logging_channel("edit")) is None:
            return
        if await db_thread(LogExclude.exists, after.channel.id):
            return

        embed = Embed(title=translations.message_edited, color=0xFFFF00, timestamp=datetime.utcnow())
        embed.add_field(name=translations.channel, value=before.channel.mention)
        embed.add_field(name=translations.author_title, value=before.author.mention)
        embed.add_field(name=translations.url, value=before.jump_url, inline=False)
        add_field(embed, translations.old_content, before.content)
        add_field(embed, translations.new_content, after.content)
        await edit_channel.send(embed=embed)

    async def on_raw_message_edit(self, channel: TextChannel, message: Optional[Message]):
        if message.guild is None:
            return
        if message.id in ignored_messages:
            ignored_messages.remove(message.id)
            return
        if (edit_channel := await self.get_logging_channel("edit")) is None:
            return
        if await db_thread(LogExclude.exists, message.channel.id):
            return

        embed = Embed(title=translations.message_edited, color=0xFFFF00, timestamp=datetime.utcnow())
        embed.add_field(name=translations.channel, value=channel.mention)
        if message is not None:
            embed.add_field(name=translations.author_title, value=message.author.mention)
            embed.add_field(name=translations.url, value=message.jump_url, inline=False)
            add_field(embed, translations.new_content, message.content)
        await edit_channel.send(embed=embed)

    async def on_message_delete(self, message: Message):
        if message.guild is None:
            return
        if message.id in ignored_messages:
            ignored_messages.remove(message.id)
            return
        if (delete_channel := await self.get_logging_channel("delete")) is None:
            return
        if await self.is_logging_channel(message.channel):
            return
        if await db_thread(LogExclude.exists, message.channel.id):
            return

        embed = Embed(title=translations.message_deleted, color=0xFF0000, timestamp=(datetime.utcnow()))
        embed.add_field(name=translations.channel, value=message.channel.mention)
        embed.add_field(name=translations.author_title, value=message.author.mention)
        add_field(embed, translations.old_content, message.content)
        if message.attachments:
            out = []
            for attachment in message.attachments:
                size = attachment.size
                for unit in "BKMG":
                    if size < 1000:
                        break
                    size /= 1000
                out.append(f"{attachment.filename} ({size:.1f} {unit})")
            embed.add_field(name=translations.attachments, value="\n".join(out), inline=False)
        await delete_channel.send(embed=embed)

    async def on_raw_message_delete(self, event: RawMessageDeleteEvent):
        if event.guild_id is None:
            return
        if event.message_id in ignored_messages:
            ignored_messages.remove(event.message_id)
            return
        if (delete_channel := await self.get_logging_channel("delete")) is None:
            return
        if await db_thread(LogExclude.exists, event.channel_id):
            return

        embed = Embed(title=translations.message_deleted, color=0xFF0000, timestamp=datetime.utcnow())
        channel: Optional[TextChannel] = self.bot.get_channel(event.channel_id)
        if channel is not None:
            if await self.is_logging_channel(channel):
                return

            embed.add_field(name=translations.channel, value=channel.mention)
            embed.add_field(name=translations.message_id, value=event.message_id, inline=False)
        await delete_channel.send(embed=embed)

    async def on_member_remove(self, member: Member):
        if (log_channel := await self.get_logging_channel("memberleave")) is None:
            return

        await log_channel.send(translations.f_member_left_server(member))

    @commands.group(aliases=["log"])
    @Permission.log_manage.check
    @guild_only()
    async def logging(self, ctx: Context):
        """
        view and change logging settings
        """

        if ctx.subcommand_passed is not None:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        edit_channel: Optional[TextChannel] = await self.get_logging_channel("edit")
        delete_channel: Optional[TextChannel] = await self.get_logging_channel("delete")
        changelog_channel: Optional[TextChannel] = await self.get_logging_channel("changelog")
        memberleave_channel: Optional[TextChannel] = await self.get_logging_channel("memberleave")
        maxage: int = await Settings.get(int, "logging_maxage", -1)

        embed = Embed(title=translations.logging, color=0x256BE6)

        if maxage != -1:
            embed.add_field(name=translations.maxage, value=translations.f_x_days(maxage), inline=False)
        else:
            embed.add_field(name=translations.maxage, value=translations.disabled, inline=False)

        if edit_channel is not None:
            mindiff: int = await Settings.get(int, "logging_edit_mindiff", 1)
            embed.add_field(name=translations.msg_edit, value=edit_channel.mention, inline=True)
            embed.add_field(name=translations.mindiff, value=str(mindiff), inline=True)
        else:
            embed.add_field(name=translations.msg_edit, value=translations.logging_disabled, inline=False)

        if delete_channel is not None:
            embed.add_field(name=translations.msg_delete, value=delete_channel.mention, inline=False)
        else:
            embed.add_field(name=translations.msg_delete, value=translations.logging_disabled, inline=False)

        if changelog_channel is not None:
            embed.add_field(name=translations.changelog, value=changelog_channel.mention, inline=False)
        else:
            embed.add_field(name=translations.changelog, value=translations.disabled, inline=False)

        if memberleave_channel is not None:
            embed.add_field(name=translations.memberleave, value=memberleave_channel.mention, inline=False)
        else:
            embed.add_field(name=translations.memberleave, value=translations.disabled, inline=False)

        await ctx.send(embed=embed)

    @logging.command(name="maxage", aliases=["ma"])
    async def logging_maxage(self, ctx: Context, days: int):
        """
        configure period after which old log entries should be deleted
        set to -1 to disable
        """

        if days != -1 and not 0 < days < (1 << 31):
            raise CommandError(translations.invalid_duration)

        await Settings.set(int, "logging_maxage", days)
        if days == -1:
            await ctx.send(translations.maxage_set_disabled)
            await send_to_changelog(ctx.guild, translations.maxage_set_disabled)
        else:
            await ctx.send(translations.f_maxage_set(days))
            await send_to_changelog(ctx.guild, translations.f_maxage_set(days))

    @logging.group(name="edit", aliases=["e"])
    async def logging_edit(self, ctx: Context):
        """
        change settings for edit event logging
        """

        if ctx.invoked_subcommand is None:
            raise UserInputError

    @logging_edit.command(name="mindist", aliases=["md"])
    async def logging_edit_mindist(self, ctx: Context, mindist: int):
        """
        change the minimum edit distance between the old and new content of the message to be logged
        """

        if mindist <= 0:
            raise CommandError(translations.min_diff_gt_zero)

        await Settings.set(int, "logging_edit_mindiff", mindist)
        await ctx.send(translations.f_edit_mindiff_updated(mindist))
        await ctx.send(translations.f_log_mindiff_updated(mindist))

    @logging_edit.command(name="channel", aliases=["ch", "c"])
    async def logging_edit_channel(self, ctx: Context, channel: TextChannel):
        """
        change logging channel for edit events
        """

        if not channel.permissions_for(channel.guild.me).send_messages:
            raise CommandError(translations.log_not_changed_no_permissions)

        await Settings.set(int, "logging_edit", channel.id)
        await ctx.send(translations.f_log_edit_updated(channel.mention))
        await send_to_changelog(ctx.guild, translations.f_log_edit_updated(channel.mention))

    @logging_edit.command(name="disable", aliases=["d"])
    async def logging_edit_disable(self, ctx: Context):
        """
        disable edit event logging
        """

        await Settings.set(int, "logging_edit", -1)
        await ctx.send(translations.log_edit_disabled)
        await send_to_changelog(ctx.guild, translations.log_edit_disabled)

    @logging.group(name="delete", aliases=["d"])
    async def logging_delete(self, ctx: Context):
        """
        change settings for delete event logging
        """

        if ctx.invoked_subcommand is None:
            raise UserInputError

    @logging_delete.command(name="channel", aliases=["ch", "c"])
    async def logging_delete_channel(self, ctx: Context, channel: TextChannel):
        """
        change logging channel for delete events
        """

        if not channel.permissions_for(channel.guild.me).send_messages:
            raise CommandError(translations.log_not_changed_no_permissions)

        await Settings.set(int, "logging_delete", channel.id)
        await ctx.send(translations.f_log_delete_updated(channel.mention))
        await send_to_changelog(ctx.guild, translations.f_log_delete_updated(channel.mention))

    @logging_delete.command(name="disable", aliases=["d"])
    async def logging_delete_disable(self, ctx: Context):
        """
        disable delete event logging
        """

        await Settings.set(int, "logging_delete", -1)
        await ctx.send(translations.log_delete_disabled)
        await send_to_changelog(ctx.guild, translations.log_delete_disabled)

    @logging.group(name="changelog", aliases=["cl", "c", "change"])
    async def logging_changelog(self, ctx: Context):
        """
        change settings for internal changelog
        """

        if ctx.invoked_subcommand is None:
            raise UserInputError

    @logging_changelog.command(name="channel", aliases=["ch", "c"])
    async def logging_changelog_channel(self, ctx: Context, channel: TextChannel):
        """
        change changelog channel
        """

        if not channel.permissions_for(channel.guild.me).send_messages:
            raise CommandError(translations.log_not_changed_no_permissions)

        await Settings.set(int, "logging_changelog", channel.id)
        await ctx.send(translations.f_log_changelog_updated(channel.mention))
        await send_to_changelog(ctx.guild, translations.f_log_changelog_updated(channel.mention))

    @logging_changelog.command(name="disable", aliases=["d"])
    async def logging_changelog_disable(self, ctx: Context):
        """
        disable changelog
        """

        await send_to_changelog(ctx.guild, translations.log_changelog_disabled)
        await Settings.set(int, "logging_changelog", -1)
        await ctx.send(translations.log_changelog_disabled)

    @logging.group(name="memberleave", aliases=["ml", "leave"])
    async def logging_memberleave(self, ctx: Context):
        """
        change settings for member leave logging
        """

        if ctx.invoked_subcommand is None:
            raise UserInputError

    @logging_memberleave.command(name="channel", aliases=["ch", "c"])
    async def logging_memberleave_channel(self, ctx: Context, channel: TextChannel):
        """
        change member leave channel
        """

        if not channel.permissions_for(channel.guild.me).send_messages:
            raise CommandError(translations.log_not_changed_no_permissions)

        await Settings.set(int, "logging_memberleave", channel.id)
        await ctx.send(translations.f_log_memberleave_updated(channel.mention))
        await send_to_changelog(ctx.guild, translations.f_log_memberleave_updated(channel.mention))

    @logging_memberleave.command(name="disable", aliases=["d"])
    async def logging_memberleave_disable(self, ctx: Context):
        """
        disable memberleave
        """

        await Settings.set(int, "logging_memberleave", -1)
        await ctx.send(translations.log_memberleave_disabled)
        await send_to_changelog(ctx.guild, translations.log_memberleave_disabled)

    @logging.group(name="exclude", aliases=["x", "ignore", "i"])
    async def logging_exclude(self, ctx: Context):
        """
        manage excluded channels
        """

        if len(ctx.message.content.lstrip(ctx.prefix).split()) > 2:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        embed = Embed(title=translations.excluded_channels, colour=0x256BE6)
        out = []
        for channel_id in await db_thread(LogExclude.all):
            channel: Optional[TextChannel] = self.bot.get_channel(channel_id)
            if channel is None:
                await db_thread(LogExclude.remove, channel_id)
            else:
                out.append(f":small_blue_diamond: {channel.mention}")
        if not out:
            embed.description = translations.no_channels_excluded
            embed.colour = 0xCF0606
        else:
            embed.description = "\n".join(out)
        await ctx.send(embed=embed)

    @logging_exclude.command(name="add", aliases=["a", "+"])
    async def logging_exclude_add(self, ctx: Context, channel: TextChannel):
        """
        exclude a channel from logging
        """

        if await db_thread(LogExclude.exists, channel.id):
            raise CommandError(translations.already_excluded)

        await db_thread(LogExclude.add, channel.id)
        await ctx.send(translations.excluded)
        await send_to_changelog(ctx.guild, translations.f_log_excluded(channel.mention))

    @logging_exclude.command(name="remove", aliases=["r", "del", "d", "-"])
    async def logging_exclude_remove(self, ctx: Context, channel: TextChannel):
        """
        remove a channel from exclude list
        """

        if not await db_thread(LogExclude.exists, channel.id):
            raise CommandError(translations.not_excluded)

        await db_thread(LogExclude.remove, channel.id)
        await ctx.send(translations.unexcluded)
        await send_to_changelog(ctx.guild, translations.f_log_unexcluded(channel.mention))
