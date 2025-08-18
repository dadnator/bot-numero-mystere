import os
import discord
from discord import app_commands
from discord.ext import commands
from keep_alive import keep_alive
import random
import asyncio
import sqlite3
from datetime import datetime

# --- TOKEN ET INTENTS ---
token = os.environ['TOKEN_BOT_DISCORD']

ID_CROUPIER = 1401471414262829066
ID_MEMBRE = 1366378672281620495
ID_SALON_JEU = 1406920988993654794

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)

active_games = {}

# --- CONNEXION À LA BASE DE DONNÉES ---
conn = sqlite3.connect("game_stats.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    joueur_id INTEGER NOT NULL,
    montant INTEGER NOT NULL,
    numero_choisi INTEGER NOT NULL,
    gagnant_id INTEGER,
    numero_resultat INTEGER,
    date TIMESTAMP NOT NULL
)
""")
conn.commit()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CheckFailure):
        await interaction.response.send_message("❌ Tu n'as pas la permission d'utiliser cette commande.", ephemeral=True)

async def end_game(interaction: discord.Interaction, game_data, original_message):
    montant = game_data["montant"]
    players = game_data["players"]

    suspense_embed = discord.Embed(
        title="🎲 Tirage en cours...",
        description="On croise les doigts 🤞🏻 !",
        color=discord.Color.greyple()
    )
    suspense_embed.set_image(url="https://images.emojiterra.com/google/noto-emoji/animated-emoji/1f3b2.gif")
    
    countdown_message = await interaction.channel.send(embed=suspense_embed)

    while True:
        for i in range(5, 0, -1):
            suspense_embed.description = f"Le résultat sera révélé dans {i} secondes..."
            await countdown_message.edit(embed=suspense_embed)
            await asyncio.sleep(1)

        mystery_number = random.randint(1, 6)
        winners = [player_id for player_id, data in players.items() if data['number'] == mystery_number]
        
        if winners:
            break
        
        suspense_embed.description = f"Le numéro tiré était **{mystery_number}**. Personne n'a choisi ce numéro. Relance du dé !"
        await countdown_message.edit(embed=suspense_embed)
        await asyncio.sleep(2)

    total_pot = montant * len(players)
    commission_montant = int(total_pot * 0.05)
    net_pot = total_pot - commission_montant
    
    win_per_person = net_pot // len(winners) if len(winners) > 0 else 0

    result_embed = discord.Embed(title="🔮 Résultat du Numéro Mystère", color=discord.Color.green())
    result_embed.add_field(name="Le Numéro Mystère était...", value=f"**{mystery_number}**!", inline=False)
    result_embed.add_field(name=" ", value="─" * 20, inline=False)

    for player_id, data in players.items():
        user = data['user']
        number = data['number']
        is_winner = player_id in winners
        
        status_emoji = "✅" if is_winner else "❌"
        status_text = f"**Gagné!** ({format(win_per_person, ',').replace(',', ' ')} kamas)" if is_winner else "**Perdu**"
        
        result_embed.add_field(name=f"{status_emoji} {user.display_name}", 
                                value=f"A choisi : **{number}** | {status_text}", 
                                inline=False)

    result_embed.add_field(name=" ", value="─" * 20, inline=False)
    result_embed.add_field(name="💰 Montant Total du Pot", value=f"**{format(total_pot, ',').replace(',', ' ')}** kamas", inline=True)
    result_embed.add_field(name="💸 Commission (5%)", value=f"**{format(commission_montant, ',').replace(',', ' ')}** kamas", inline=True)
    result_embed.add_field(name=" ", value="─" * 20, inline=False)
    
    if len(winners) == 1:
        winner_user = None
        try:
            winner_user = await bot.fetch_user(winners[0])
        except discord.NotFound:
            pass

        if winner_user:
            result_embed.add_field(name="🏆 Gagnant", value=f"{winner_user.mention} remporte **{format(win_per_person, ',').replace(',', ' ')}** kamas !", inline=False)
        else:
            result_embed.add_field(name="🏆 Gagnant", value=f"<@{winners[0]}> remporte **{format(win_per_person, ',').replace(',', ' ')}** kamas ! (Utilisateur introuvable)", inline=False)
    elif len(winners) > 1:
        mentions = " ".join([f"<@{w_id}>" for w_id in winners])
        result_embed.add_field(name="🏆 Gagnants (Égalité)", value=f"{mentions} se partagent le gain et reçoivent **{format(win_per_person, ',').replace(',', ' ')}** kamas chacun.", inline=False)
    
    await countdown_message.edit(embed=result_embed, view=None)
    await original_message.delete()
    
    now = datetime.utcnow()
    try:
        for player_id, data in players.items():
            winner_to_log = winners[0] if winners else None
            c.execute("INSERT INTO games (game_id, joueur_id, montant, numero_choisi, gagnant_id, numero_resultat, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (original_message.id, player_id, montant, data['number'], winner_to_log, mystery_number, now))
        conn.commit()
    except Exception as e:
        print("Erreur base de données:", e)

    active_games.pop(original_message.id, None)

class GameView(discord.ui.View):
    def __init__(self, message_id, player_count, montant):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.player_count = player_count
        self.montant = montant
        self.chosen_numbers = {}
        self.croupier = None
        self.add_number_buttons()

    def add_number_buttons(self):
        self.clear_items()
        
        for i in range(1, 7):
            button = discord.ui.Button(label=str(i), style=discord.ButtonStyle.secondary, custom_id=f"number_{i}")
            button.callback = self.choose_number_callback
            if i in self.chosen_numbers.values():
                button.disabled = True
                button.style = discord.ButtonStyle.danger
            self.add_item(button)

        cancel_button = discord.ui.Button(label="❌ Annuler", style=discord.ButtonStyle.red, custom_id="cancel_game")
        cancel_button.callback = self.cancel_game_callback
        self.add_item(cancel_button)
        
        if len(self.chosen_numbers) >= 2 and not self.croupier:
            join_croupier_button = discord.ui.Button(label="🤝 Rejoindre en tant que Croupier", style=discord.ButtonStyle.secondary, custom_id="join_croupier")
            join_croupier_button.callback = self.join_croupier_callback
            self.add_item(join_croupier_button)

    async def choose_number_callback(self, interaction: discord.Interaction):
        button = next((item for item in self.children if isinstance(item, discord.ui.Button) and item.custom_id == interaction.data['custom_id']), None)
        if not button:
            return

        user_id = interaction.user.id
        number = int(button.custom_id.split('_')[1])
        game_data = active_games.get(self.message_id)

        if user_id in self.chosen_numbers.keys():
            await interaction.response.send_message("❌ Tu as déjà choisi un numéro pour cette partie.", ephemeral=True)
            return

        if number in self.chosen_numbers.values():
            await interaction.response.send_message("❌ Ce numéro est déjà pris. Choisi un autre numéro.", ephemeral=True)
            return
            
        self.chosen_numbers[user_id] = number
        game_data["players"][user_id] = {"user": interaction.user, "number": number}

        self.add_number_buttons()

        embed = interaction.message.embeds[0]
        
        joined_players_list = "\n".join([f"{p_data['user'].mention} a choisi le numéro **{p_data['number']}**" for p_data in game_data["players"].values()])
        embed.set_field_at(0, name="Joueurs inscrits", value=joined_players_list if joined_players_list else "...", inline=False)
        embed.set_field_at(1, name="Status", value=f"**{len(game_data['players'])}/{self.player_count}** joueurs inscrits. En attente...", inline=False)
        
        if len(game_data['players']) >= 2:
            embed.set_footer(text="Un croupier peut maintenant lancer la partie.")

        await interaction.response.edit_message(embed=embed, view=self, allowed_mentions=discord.AllowedMentions(users=True))

    async def cancel_game_callback(self, interaction: discord.Interaction):
        game_data = active_games.get(self.message_id)
        if interaction.user.id not in self.chosen_numbers.keys():
            await interaction.response.send_message("❌ Seuls les joueurs inscrits peuvent annuler la partie.", ephemeral=True)
            return

        active_games.pop(self.message_id)
        
        embed = interaction.message.embeds[0]
        embed.title = "❌ Partie annulée"
        embed.description = f"La partie a été annulée par {interaction.user.mention}."
        embed.color = discord.Color.red()

        await interaction.response.edit_message(embed=embed, view=None, allowed_mentions=discord.AllowedMentions(users=True))

    async def join_croupier_callback(self, interaction: discord.Interaction):
        role_croupier = interaction.guild.get_role(ID_CROUPIER)
        
        if not role_croupier or role_croupier not in interaction.user.roles:
            await interaction.response.send_message("❌ Tu n'as pas le rôle de `croupier` pour rejoindre cette partie.", ephemeral=True)
            return
            
        game_data = active_games.get(self.message_id)
        self.croupier = interaction.user
        game_data["croupier"] = interaction.user
        
        self.clear_items()
        start_game_button = discord.ui.Button(label="🎰 Lancer la partie !", style=discord.ButtonStyle.success, custom_id="start_game_button")
        start_game_button.callback = self.start_game_button_callback
        self.add_item(start_game_button)
        
        embed = interaction.message.embeds[0]
        embed.set_field_at(1, name="Status", value=f"✅ Prêt à jouer ! Croupier : {interaction.user.mention}", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=self, allowed_mentions=discord.AllowedMentions(users=True))
        
    async def start_game_button_callback(self, interaction: discord.Interaction):
        game_data = active_games.get(self.message_id)
        
        if interaction.user.id != game_data["croupier"].id:
            await interaction.response.send_message("❌ Seul le croupier peut lancer la partie.", ephemeral=True)
            return
            
        await interaction.response.defer()
        
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(view=self)
        
        original_message = await interaction.channel.fetch_message(self.message_id)
        await end_game(interaction, game_data, original_message)
        
    async def on_timeout(self):
        game_data = active_games.get(self.message_id)
        if game_data and len(game_data["players"]) < 2:
            try:
                message = await self.ctx.channel.fetch_message(self.message_id)
                embed = message.embeds[0]
                embed.title = "❌ Partie expirée"
                embed.description = "La partie a expiré car il n'y a pas assez de joueurs."
                embed.color = discord.Color.red()
                await message.edit(embed=embed, view=None)
            except discord.NotFound:
                pass
            active_games.pop(self.message_id, None)

# --- COMMANDES ---
@bot.tree.command(name="duel", description="Lancer une partie de Numéro Mystère.")
@app_commands.describe(montant="Montant misé en kamas")
async def startgame(interaction: discord.Interaction, montant: int):
    if interaction.channel.id != ID_SALON_JEU:
        await interaction.response.send_message("❌ Cette commande ne peut être utilisée que dans le salon #『🎲』dés.", ephemeral=True)
        return

    if montant <= 0:
        await interaction.response.send_message("❌ Le montant doit être supérieur à 0.", ephemeral=True)
        return
    
    for message_id, game_data in active_games.items():
        if interaction.user.id in game_data["players"].keys():
            await interaction.response.send_message("❌ Tu participes déjà à une autre partie.", ephemeral=True)
            return

    MAX_JOUEURS = 6
    embed = discord.Embed(
        title="🔮 Nouvelle Partie de Numéro Mystère",
        description=f"**{interaction.user.mention}** a lancé une partie pour **{montant:,.0f}".replace(",", " ") + " kamas** par personne.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Joueurs inscrits", value="...", inline=False)
    embed.add_field(name="Status", value=f"**0/{MAX_JOUEURS}** joueurs inscrits. En attente...", inline=False)
    embed.set_footer(text="Clique sur un numéro pour t'inscrire et faire un choix.")

    view = GameView(None, MAX_JOUEURS, montant)
    
    ping_content = ""
    role_membre = interaction.guild.get_role(ID_MEMBRE)
    if role_membre:
        ping_content = f"{role_membre.mention} — Une nouvelle partie est prête ! Rejoignez-la !"
    
    await interaction.response.send_message(
        content=ping_content,
        embed=embed,
        view=view,
        ephemeral=False,
        allowed_mentions=discord.AllowedMentions(roles=True, users=True)
    )

    sent_message = await interaction.original_response()
    view.message_id = sent_message.id
    active_games[sent_message.id] = {"players": {}, "montant": montant, "croupier": None, "player_limit": MAX_JOUEURS}
    await sent_message.edit(view=view)

# --- STATS VIEWS AND COMMANDS ---
class StatsView(discord.ui.View):
    def __init__(self, ctx, entries, page=0):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.entries = entries
        self.page = page
        self.entries_per_page = 10
        self.max_page = (len(entries) - 1) // self.entries_per_page
        self.update_buttons()

    def update_buttons(self):
        self.first_page.disabled = self.page == 0
        self.prev_page.disabled = self.page == 0
        self.next_page.disabled = self.page == self.max_page
        self.last_page.disabled = self.page == self.max_page

    def get_embed(self):
        embed = discord.Embed(title="📊 Statistiques globales des parties", color=discord.Color.gold())
        start = self.page * self.entries_per_page
        end = start + self.entries_per_page
        slice_entries = self.entries[start:end]

        if not slice_entries:
            embed.description = "Aucune donnée à afficher."
            return embed

        description = ""
        for i, (user_id, total_parties, total_mises, total_gagnes, victoires, winrate) in enumerate(slice_entries):
            rank = self.page * self.entries_per_page + i + 1
            description += (
                f"**#{rank}** <@{user_id}> — "
                f"💰 **Misés** : **`{total_mises:,.0f}`".replace(",", " ") + " kamas** | "
                f"🏆 **Gagnés** : **`{total_gagnes:,.0f}`".replace(",", " ") + " kamas** | "
                f"**🎯 Winrate** : **`{winrate:.1f}%`** (**{victoires}**/**{total_parties}**)\n"
            )
            if i < len(slice_entries) - 1:
                description += "─" * 20 + "\n"

        embed.description = description
        embed.set_footer(text=f"Page {self.page + 1}/{self.max_page + 1}")
        return embed

    @discord.ui.button(label="⏮️", style=discord.ButtonStyle.secondary)
    async def first_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.max_page:
            self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

    @discord.ui.button(label="⏭️", style=discord.ButtonStyle.secondary)
    async def last_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.max_page
        self.update_buttons()
        await interaction.response.edit_message(embed=self.get_embed(), view=self)

@bot.tree.command(name="statsall", description="Affiche les stats du jeu de Numéro Mystère.")
async def statsall(interaction: discord.Interaction):
    if interaction.channel.id != ID_SALON_JEU:
        await interaction.response.send_message("❌ Cette commande ne peut être utilisée que dans le salon #『🎲』dés.", ephemeral=True)
        return

    c.execute("""
    WITH GameStats AS (
      SELECT
        game_id,
        SUM(montant) AS total_pot,
        COUNT(DISTINCT gagnant_id) AS num_winners
      FROM games
      GROUP BY game_id
    )
    SELECT
      g.joueur_id,
      COUNT(g.joueur_id) AS total_parties,
      SUM(g.montant) AS total_mises,
      SUM(
        CASE
          WHEN g.gagnant_id = g.joueur_id THEN
            (gs.total_pot * 0.95) / gs.num_winners
          ELSE
            0
        END
      ) AS total_gagnes,
      SUM(CASE WHEN g.gagnant_id = g.joueur_id THEN 1 ELSE 0 END) AS victoires
    FROM games g
    JOIN GameStats gs ON g.game_id = gs.game_id
    GROUP BY g.joueur_id
    ORDER BY total_gagnes DESC
    """)
    
    data = c.fetchall()

    stats = []
    for user_id, total_parties, total_mises, total_gagnes, victoires in data:
        winrate = (victoires / total_parties * 100) if total_parties > 0 else 0.0
        stats.append((user_id, total_parties, total_mises, total_gagnes, victoires, winrate))

    if not stats:
        await interaction.response.send_message("Aucune donnée statistique disponible.", ephemeral=True)
        return

    view = StatsView(interaction, stats)
    await interaction.response.send_message(embed=view.get_embed(), view=view, ephemeral=False)

@bot.tree.command(name="mystats", description="Affiche tes statistiques de Numéro Mystère.")
async def mystats(interaction: discord.Interaction):
    user_id = interaction.user.id

    c.execute("""
    SELECT
      SUM(g.montant) AS total_mise,
      SUM(
        CASE
          WHEN g.gagnant_id = g.joueur_id THEN
            (gs.total_pot * 0.95) / gs.num_winners
          ELSE
            0
        END
      ) AS kamas_gagnes,
      SUM(CASE WHEN g.gagnant_id = g.joueur_id THEN 1 ELSE 0 END) AS victoires,
      COUNT(*) AS total_parties
    FROM games g
    JOIN (
      SELECT
        game_id,
        SUM(montant) AS total_pot,
        COUNT(DISTINCT gagnant_id) AS num_winners
      FROM games
      GROUP BY game_id
    ) gs ON g.game_id = gs.game_id
    WHERE g.joueur_id = ?
    GROUP BY g.joueur_id;
    """, (user_id,))
    
    stats_data = c.fetchone()
    
    if not stats_data:
        embed = discord.Embed(
            title="📊 Tes Statistiques de Numéro Mystère",
            description="❌ Tu n'as pas encore participé à une partie.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
        
    mises, kamas_gagnes, victoires, total_parties = stats_data
    winrate = (victoires / total_parties * 100) if total_parties > 0 else 0.0

    embed = discord.Embed(
        title=f"📊 Statistiques de {interaction.user.display_name}",
        description="Voici un résumé de tes performances au jeu du Numéro Mystère.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Total misé", value=f"**{mises:,.0f}".replace(",", " ") + " kamas**", inline=False)
    embed.add_field(name=" ", value="─" * 3, inline=False)
    embed.add_field(name="Total gagné", value=f"**{kamas_gagnes:,.0f}".replace(",", " ") + " kamas**", inline=False)
    embed.add_field(name=" ", value="─" * 20, inline=False)
    embed.add_field(name="Parties jouées", value=f"**{total_parties}**", inline=True)
    embed.add_field(name=" ", value="─" * 3, inline=False)
    embed.add_field(name="Victoires", value=f"**{victoires}**", inline=True)
    embed.add_field(name=" ", value="─" * 3, inline=False)
    embed.add_field(name="Taux de victoire", value=f"**{winrate:.1f}%**", inline=False)

    embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else None)
    embed.set_footer(text="Bonne chance pour tes prochaines parties !")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    print(f"{bot.user} est prêt !")
    try:
        await bot.tree.sync()
        print("✅ Commandes synchronisées.")
    except Exception as e:
        print(f"Erreur : {e}")

keep_alive()
bot.run(token)
