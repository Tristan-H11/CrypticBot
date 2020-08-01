from typing import Optional, Tuple

from PyDrocsid.database import db_thread, db
from PyDrocsid.emoji_converter import EmojiConverter
from PyDrocsid.events import StopEventHandling
from PyDrocsid.translations import translations
from discord import Message, Role, PartialEmoji, TextChannel, Member, NotFound
from discord.ext import commands
from discord.ext.commands import Cog, Bot, guild_only, Context, CommandError, UserInputError

from models.reactionrole import ReactionRole
from permissions import Permission
from util import send_to_changelog


class RoleNotFound(Exception):
    pass


async def get_role(message: Message, emoji: PartialEmoji) -> Tuple[ReactionRole, Role]:
    link: Optional[ReactionRole] = await db_thread(ReactionRole.get, message.channel.id, message.id, str(emoji))
    if link is None:
        raise RoleNotFound
    role: Optional[Role] = link.get_role(message.guild)
    if role is None:
        raise RoleNotFound
    return link, role


class ReactionRoleCog(Cog, name="ReactionRole"):
    def __init__(self, bot: Bot):
        self.bot = bot

    async def on_raw_reaction_add(self, message: Message, emoji: PartialEmoji, member: Member):
        if member.bot or message.guild is None:
            return

        try:
            link, role = await get_role(message, emoji)
        except RoleNotFound:
            return

        try:
            if link.reverse:
                await member.remove_roles(role)
            else:
                await member.add_roles(role)
            if link.auto_remove:
                await message.remove_reaction(emoji, member)
        except NotFound:
            pass
        raise StopEventHandling

    async def on_raw_reaction_remove(self, message: Message, emoji: PartialEmoji, member: Member):
        if member.bot or message.guild is None:
            return

        try:
            link, role = await get_role(message, emoji)
        except RoleNotFound:
            return
        if link.auto_remove:
            return

        try:
            if link.reverse:
                await member.add_roles(role)
            else:
                await member.remove_roles(role)
        except NotFound:
            pass
        raise StopEventHandling

    @commands.group(aliases=["rr"])
    @Permission.manage_rr.check
    @guild_only()
    async def reactionrole(self, ctx: Context):
        """
        manage reactionrole
        """

        if ctx.invoked_subcommand is None:
            raise UserInputError

    @reactionrole.command(name="list", aliases=["l", "?"])
    async def reactionrole_list(self, ctx: Context, msg: Optional[Message] = None):
        """
        list configured reactionrole links
        """

        if msg is None:
            channels = {}
            for link in await db_thread(db.all, ReactionRole):  # type: ReactionRole
                channel: Optional[TextChannel] = ctx.guild.get_channel(link.channel_id)
                if channel is None:
                    await db_thread(db.delete, link)
                    continue
                try:
                    msg: Message = await channel.fetch_message(link.message_id)
                except NotFound:
                    await db_thread(db.delete, link)
                    continue
                if ctx.guild.get_role(link.role_id) is None:
                    await db_thread(db.delete, link)
                    continue
                channels.setdefault(channel, {}).setdefault(msg.jump_url, set())
                channels[channel][msg.jump_url].add(link.emoji)

            if not channels:
                await ctx.send(translations.no_reactionrole_links)
            else:
                await ctx.send(
                    "\n\n".join(
                        f"{channel.mention}:\n"
                        + "\n".join(url + " " + " ".join(emojis) for url, emojis in messages.items())
                        for channel, messages in channels.items()
                    )
                )
        else:
            out = []
            for link in await db_thread(
                db.all, ReactionRole, channel_id=msg.channel.id, message_id=msg.id
            ):  # type: ReactionRole
                channel: Optional[TextChannel] = ctx.guild.get_channel(link.channel_id)
                if channel is None or await channel.fetch_message(link.message_id) is None:
                    await db_thread(db.delete, link)
                    continue
                role: Optional[Role] = ctx.guild.get_role(link.role_id)
                if role is None:
                    await db_thread(db.delete, link)
                    continue
                flags = [translations.rr_reverse] * link.reverse + [translations.rr_auto_remove] * link.auto_remove
                out.append(translations.f_rr_link(link.emoji, role.name))
                if flags:
                    out[-1] += f" ({', '.join(flags)})"
            if not out:
                await ctx.send(translations.no_reactionrole_links_for_msg)
            else:
                await ctx.send("\n".join(out))

    @reactionrole.command(name="add", aliases=["a", "+"])
    async def reactionrole_add(
        self, ctx: Context, msg: Message, emoji: EmojiConverter, role: Role, reverse: bool, auto_remove: bool
    ):
        """
        add a new reactionrole link
        """

        emoji: PartialEmoji

        if await db_thread(ReactionRole.get, msg.channel.id, msg.id, str(emoji)) is not None:
            raise CommandError(translations.rr_link_already_exists)
        if not msg.channel.permissions_for(msg.guild.me).add_reactions:
            raise CommandError(translations.rr_link_not_created_no_permissions)

        if role >= ctx.me.top_role:
            raise CommandError(translations.f_link_not_created_too_high(role, ctx.me.top_role))
        if role.managed or role.is_default():
            raise CommandError(translations.f_link_not_created_managed_role(role))

        await db_thread(ReactionRole.create, msg.channel.id, msg.id, str(emoji), role.id, reverse, auto_remove)
        await msg.add_reaction(emoji)
        await ctx.send(translations.rr_link_created)
        await send_to_changelog(ctx.guild, translations.f_log_rr_link_created(emoji, role, msg.jump_url))

    @reactionrole.command(name="remove", aliases=["r", "del", "d", "-"])
    async def reactionrole_remove(self, ctx: Context, msg: Message, emoji: EmojiConverter):
        """
        remove a reactionrole link
        """

        emoji: PartialEmoji

        if (link := await db_thread(ReactionRole.get, msg.channel.id, msg.id, str(emoji))) is None:
            raise CommandError(translations.rr_link_not_found)

        await db_thread(db.delete, link)
        for reaction in msg.reactions:
            if str(emoji) == str(reaction.emoji):
                await reaction.clear()
        await ctx.send(translations.rr_link_removed)
        await send_to_changelog(ctx.guild, translations.f_log_rr_link_removed(emoji, msg.jump_url))
