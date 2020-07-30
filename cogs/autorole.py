from typing import Optional

from PyDrocsid.database import db_thread
from PyDrocsid.translations import translations
from discord import Embed, Role, Member
from discord.ext import commands
from discord.ext.commands import Cog, Bot, guild_only, Context, UserInputError, CommandError

from models.autorole import AutoRole
from permissions import Permission


class AutoRoleCog(Cog, name="AutoRole"):
    def __init__(self, bot: Bot):
        self.bot = bot

    async def on_member_join(self, member: Member):
        await member.add_roles(*filter(lambda r: r, map(member.guild.get_role, await db_thread(AutoRole.all))))

    @commands.group(aliases=["ar"])
    @Permission.manage_ar.check
    @guild_only()
    async def autorole(self, ctx: Context):
        """
        configure autorole
        """

        if ctx.subcommand_passed is not None:
            if ctx.invoked_subcommand is None:
                raise UserInputError
            return

        embed = Embed(title=translations.autorole, colour=0x256BE6)
        out = []
        for role_id in await db_thread(AutoRole.all):
            role: Optional[Role] = ctx.guild.get_role(role_id)
            if role is None:
                await db_thread(AutoRole.remove, role_id)
            else:
                out.append(f":small_orange_diamond: {role.mention}")
        if not out:
            embed.description = translations.no_autorole
            embed.colour = 0xCF0606
        else:
            embed.description = "\n".join(out)
        await ctx.send(embed=embed)

    @autorole.command(name="add", aliases=["a", "+"])
    async def autorole_add(self, ctx: Context, *, role: Role):
        """
        exclude a channel from logging
        """

        if await db_thread(AutoRole.exists, role.id):
            raise CommandError(translations.ar_already_set)

        await db_thread(AutoRole.add, role.id)
        await ctx.send(translations.ar_added)
        # await send_to_changelog(ctx.guild, translations.f_log_excluded(channel.mention))

    @autorole.command(name="remove", aliases=["r", "del", "d", "-"])
    async def autorole_remove(self, ctx: Context, role: Role):
        """
        remove a channel from exclude list
        """

        if not await db_thread(AutoRole.exists, role.id):
            raise CommandError(translations.ar_not_set)

        await db_thread(AutoRole.remove, role.id)
        await ctx.send(translations.ar_removed)
        # await send_to_changelog(ctx.guild, translations.f_log_unexcluded(channel.mention))
