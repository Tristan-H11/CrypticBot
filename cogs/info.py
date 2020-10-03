import re
from typing import Optional, List

from PyDrocsid.settings import Settings
from PyDrocsid.translations import translations
from discord import Embed, Guild, Status, Game, Member, Message
from discord import Role
from discord.ext import commands
from discord.ext.commands import Cog, Bot, guild_only, Context, UserInputError


class InfoCog(Cog, name="Server Information"):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.current_status = 0

    async def on_ready(self):
        await self.bot.change_presence(status=Status.online, activity=Game(name=translations.profile_status))

    async def on_message(self, message: Message):
        if message.guild is None:
            return

        role_mentions = {role.id for role in message.role_mentions}
        quote_mentions = set()
        for line in message.content.splitlines():
            mentions = {int(match.group(1)) for match in re.finditer(r"<@&(\d+)>", line)}
            if line.startswith("> "):
                quote_mentions.update(mentions)
            else:
                role_mentions.difference_update(mentions)
        if role_mentions & quote_mentions:
            await message.channel.send(translations.f_quote_remove_mentions(message.author.mention))

    @commands.group()
    @guild_only()
    async def server(self, ctx: Context):
        """
        displays information about this discord server
        """

        if ctx.subcommand_passed is not None:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        guild: Guild = ctx.guild
        embed = Embed(title=guild.name, description=translations.info_description, color=0x005180)
        embed.set_thumbnail(url=guild.icon_url)
        created = guild.created_at.date()
        embed.add_field(name=translations.creation_date, value=f"{created.day}.{created.month}.{created.year}")
        online_count = sum([m.status != Status.offline for m in guild.members])
        embed.add_field(
            name=translations.f_cnt_members(guild.member_count), value=translations.f_cnt_online(online_count)
        )
        embed.add_field(name=translations.owner, value=guild.owner.mention)

        async def get_role(role_name) -> Optional[Role]:
            return guild.get_role(await Settings.get(int, role_name + "_role"))

        role: Role
        if (role := await get_role("head")) is not None and role.members:
            embed.add_field(
                name=translations.f_cnt_heads(len(role.members)),
                value="\n".join(":small_orange_diamond: " + m.mention for m in role.members),
            )
        if (role := await get_role("head_assistant")) is not None and role.members:
            embed.add_field(
                name=translations.f_cnt_assistants(len(role.members)),
                value="\n".join(":small_orange_diamond: " + m.mention for m in role.members),
            )

        bots = [m for m in guild.members if m.bot]
        bots_online = sum([m.status != Status.offline for m in bots])
        embed.add_field(name=translations.f_cnt_bots(len(bots)), value=translations.f_cnt_online(bots_online))

        await ctx.send(embed=embed)

    @server.command(name="bots")
    async def server_bots(self, ctx: Context):
        """
        list all bots on the server
        """

        guild: Guild = ctx.guild
        embed = Embed(title=translations.bots, color=0x005180)
        online: List[Member] = []
        offline: List[Member] = []
        for member in guild.members:  # type: Member
            if member.bot:
                [offline, online][member.status != Status.offline].append(member)

        if not online + offline:
            embed.colour = 0xCF0606
            embed.description = translations.no_bots
            await ctx.send(embed=embed)
            return

        if online:
            embed.add_field(
                name=translations.online, value="\n".join(":small_orange_diamond: " + m.mention for m in online)
            )
        if offline:
            embed.add_field(
                name=translations.offline, value="\n".join(":small_blue_diamond: " + m.mention for m in offline)
            )
        await ctx.send(embed=embed)
