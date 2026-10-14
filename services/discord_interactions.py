import asyncio
import os

import data_manager
import scryfall
from modules.market_analyzer import portfolio_summary


def create_bot():
    import discord
    from discord import app_commands
    from discord.ext import commands, tasks

    intents = discord.Intents.none()
    bot = commands.Bot(command_prefix="!", intents=intents)

    @tasks.loop(hours=24)
    async def daily_report():
        from discord_bot import notify_daily_summary
        await asyncio.to_thread(notify_daily_summary)

    @daily_report.before_loop
    async def before_daily_report():
        await bot.wait_until_ready()

    @bot.tree.command(name="prix", description="Consulter le dernier prix Scryfall d'une carte")
    @app_commands.describe(carte="Nom de la carte")
    async def price_command(interaction: discord.Interaction, carte: str):
        await interaction.response.defer(thinking=True)
        card = await asyncio.to_thread(scryfall.get_card_by_name, carte)
        if not card:
            await interaction.followup.send("Carte introuvable dans Scryfall.", ephemeral=True)
            return
        prices = card.get("prices") or {}
        normal = prices.get("eur") or "indisponible"
        foil = prices.get("eur_foil") or "indisponible"
        await interaction.followup.send(f"{card.get('name', carte)} · EUR {normal} · Foil {foil}")

    @bot.tree.command(name="portfolio", description="Afficher la synthèse du portefeuille")
    async def portfolio_command(interaction: discord.Interaction):
        summary = portfolio_summary(data_manager.get_collection())
        await interaction.response.send_message(
            f"Stock : {summary['stock_value']:.2f} € · P&L latent : "
            f"{summary['unrealized_profit']:.2f} € · P&L réalisé : "
            f"{summary['realized_profit']:.2f} € · {summary['for_sale_count']} référence(s).",
            ephemeral=True,
        )

    @bot.tree.command(name="watch", description="Ajouter une carte à la watchlist")
    @app_commands.describe(carte="Nom de la carte", prix_max="Prix d'achat maximal en euros")
    async def watch_command(
        interaction: discord.Interaction,
        carte: str,
        prix_max: app_commands.Range[float, 0, 100000] | None = None,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)
        card = await asyncio.to_thread(scryfall.get_card_by_name, carte)
        if not card:
            await interaction.followup.send("Carte introuvable dans Scryfall.", ephemeral=True)
            return
        watchlist = data_manager.get_watchlist()
        if any(item.get("scryfall_id") == card.get("id") for item in watchlist):
            await interaction.followup.send(f"{card['name']} est déjà suivie.", ephemeral=True)
            return
        watchlist.append({
            "scryfall_id": card["id"], "name": card["name"],
            "set_code": card.get("set", ""), "foil": False,
            "language": "FR", "condition": "NM",
            "max_buy_price": prix_max,
        })
        data_manager.save_watchlist(watchlist)
        await interaction.followup.send(f"{card['name']} ajoutée à la watchlist.", ephemeral=True)

    @bot.event
    async def on_ready():
        await bot.tree.sync()
        if not daily_report.is_running():
            daily_report.start()

    return bot


def run_bot():
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN n'est pas configuré.")
    create_bot().run(token)