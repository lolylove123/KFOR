import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import datetime
import re
import random
import asyncio
import os
from dotenv import load_dotenv
from typing import Literal

load_dotenv()

# --- НАСТРОЙКИ БД ---
DB_NAME = "clan_base.db"

# --- УНИКАЛЬНЫЕ ТЕКСТЫ ДЛЯ РЕЖИМОВ ---
unique_messages = {
    "tvt": "Прожмите, кто придет на игру, а кто нет. \nНе забудьте скачать моды заранее.",
    "ltvt": "Прожмите, кто придет на игру, а кто нет. \nНе забудьте скачать моды заранее.",
    "ttvt": "Прожмите, кто придет на игру, а кто нет. \nНе забудьте скачать моды заранее."
}

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def has_required_role(member):
    """Проверяет, есть ли у пользователя нужные роли."""
    role_names = [role.name for role in member.roles]
    return "NATO" in role_names or "NATOk" in role_names

class VoterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def update_activity(self, user_id):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO members (user_id, last_active) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_active = ?",
                (user_id, datetime.date.today().isoformat(), datetime.date.today().isoformat())
            )
            await db.commit()

    async def get_poll_results_text(self, message_id, mode, date, poll_num):
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT choice FROM votes WHERE message_id = ?", (message_id,)) as cursor:
                votes = await cursor.fetchall()
        
        results = {"Иду": 0, "Не иду": 0, "50/50": 0}
        for (choice,) in votes:
            if choice in results: results[choice] += 1
        
        unique_text = unique_messages.get(mode.lower(), "")
        
        content = (
            f"\n**⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯**\n"
            f"**Регистрация на {mode.upper()} #{poll_num}**\n"
            f"**Дата проведения:** {date}\n\n"
            f"Проголосовали: {len(votes)}\n"
            f"✅ Иду: {results['Иду']}\n"
            f"❌ Не иду: {results['Не иду']}\n"
            f"👨‍🦽 50/50: {results['50/50']}\n\n"
            f"@everyone {unique_text}"
        )
        return content

    @discord.ui.button(label="Иду", style=discord.ButtonStyle.green, custom_id="vote_yes")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cast_vote(interaction, "Иду")

    @discord.ui.button(label="Не иду", style=discord.ButtonStyle.red, custom_id="vote_no")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cast_vote(interaction, "Не иду")

    @discord.ui.button(label="50/50", style=discord.ButtonStyle.secondary, custom_id="vote_maybe")
    async def maybe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cast_vote(interaction, "50/50")

    @discord.ui.button(label="Посмотреть участников", style=discord.ButtonStyle.primary, custom_id="vote_view")
    async def view(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id, choice FROM votes WHERE message_id = ?", (interaction.message.id,)) as cursor:
                votes = await cursor.fetchall()
        
        if not votes:
            return await interaction.response.send_message("Голосов пока нет.", ephemeral=True)

        categories = {"Иду": [], "Не иду": [], "50/50": []}
        for uid, choice in votes:
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else f"ID: {uid}"
            categories[choice].append(name)

        embed = discord.Embed(title=f"📋 Участники опроса", color=discord.Color.blue())
        embed.add_field(name="✅ Идут", value="\n".join(categories["Иду"]) or "—", inline=True)
        embed.add_field(name="❌ Не идут", value="\n".join(categories["Не иду"]) or "—", inline=True)
        embed.add_field(name="👨‍🦽 50/50", value="\n".join(categories["50/50"]) or "—", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def cast_vote(self, interaction: discord.Interaction, choice: str):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO votes (message_id, user_id, choice) VALUES (?, ?, ?) ON CONFLICT(message_id, user_id) DO UPDATE SET choice = ?",
                (interaction.message.id, interaction.user.id, choice, choice)
            )
            await db.commit()
            async with db.execute("SELECT poll_id FROM polls WHERE message_id = ?", (interaction.message.id,)) as cursor:
                poll_row = await cursor.fetchone()
        
        await self.update_activity(interaction.user.id)
        
        lines = interaction.message.content.strip().split('\n')
        header_line = [l for l in lines if "Регистрация на" in l][0]
        date_line = [l for l in lines if "Дата проведения:" in l][0]
        
        mode = header_line.split(' ')[2]
        poll_num = poll_row[0] if poll_row else "?"
        date = date_line.replace("**Дата проведения:** ", "")

        new_content = await self.get_poll_results_text(interaction.message.id, mode, date, poll_num)
        await interaction.response.edit_message(content=new_content)

class ClanBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # ВАЖНО: Должно быть включено в панели разработчика Discord
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("""CREATE TABLE IF NOT EXISTS settings 
                (guild_id INTEGER PRIMARY KEY, poll_channel INTEGER, log_channel INTEGER, admin_role INTEGER, ignore_role INTEGER)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS members 
                (user_id INTEGER PRIMARY KEY, last_active TEXT)""")
            await db.execute("""CREATE TABLE IF NOT EXISTS votes 
                (message_id INTEGER, user_id INTEGER, choice TEXT, PRIMARY KEY(message_id, user_id))""")
            await db.execute("""CREATE TABLE IF NOT EXISTS polls 
                (poll_id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, guild_id INTEGER, is_active INTEGER)""")
            await db.commit()
        self.add_view(VoterView())
        self.check_activity.start()

    async def on_ready(self):
        print(f'Бот {self.user} запущен и готов!')
        await self.tree.sync()

    # --- АВТОМАТИЧЕСКОЕ ДОБАВЛЕНИЕ НОВИЧКОВ ---
    async def on_member_join(self, member):
        if has_required_role(member):
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute(
                    "INSERT OR IGNORE INTO members (user_id, last_active) VALUES (?, ?)",
                    (member.id, datetime.date.today().isoformat())
                )
                await db.commit()
            print(f"Добавлен новый участник: {member.display_name}")

    # --- ПРОВЕРКА ПРИ ВЫХОДЕ ---
    async def on_member_remove(self, member):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM members WHERE user_id = ?", (member.id,))
            await db.commit()
        print(f"Участник покинул сервер: {member.display_name}")

    @tasks.loop(hours=24)
    async def check_activity(self):
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT guild_id, log_channel, ignore_role FROM settings") as cursor:
                configs = await cursor.fetchall()
            
            for g_id, log_id, ignore_id in configs:
                guild = self.get_guild(g_id)
                if not guild: continue
                channel = guild.get_channel(log_id)
                if not channel: continue
                
                async with db.execute("SELECT user_id, last_active FROM members") as m_cursor:
                    members_data = await m_cursor.fetchall()
                
                for u_id, last_date_str in members_data:
                    member = guild.get_member(u_id)
                    
                    # Если человека больше нет на сервере или у него забрали роли NATO/NATOk
                    if not member or not has_required_role(member):
                        await db.execute("DELETE FROM members WHERE user_id = ?", (u_id,))
                        await db.commit()
                        continue

                    if ignore_id and member.get_role(ignore_id): continue
                    
                    last_date = datetime.date.fromisoformat(last_date_str)
                    delta = (datetime.date.today() - last_date).days
                    if delta >= 30 and delta % 30 == 0:
                        await channel.send(f"⚠️ **{member.display_name}** не участвовал в активностях уже {delta} дней!")

bot = ClanBot()

async def get_admin_role(guild_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT admin_role FROM settings WHERE guild_id = ?", (guild_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

# --- КОМАНДЫ ---

@bot.tree.command(name="update_members", description="Синхронизировать базу участников с текущим составом NATO/NATOk")
async def update_members(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    admin_role_id = await get_admin_role(interaction.guild.id)
    if not admin_role_id or not interaction.user.get_role(admin_role_id):
        return await interaction.followup.send("❌ У вас нет прав для этой команды.", ephemeral=True)

    added_count = 0
    async with aiosqlite.connect(DB_NAME) as db:
        for member in interaction.guild.members:
            if has_required_role(member):
                # Проверяем, есть ли он уже
                async with db.execute("SELECT 1 FROM members WHERE user_id = ?", (member.id,)) as cursor:
                    if not await cursor.fetchone():
                        await db.execute(
                            "INSERT INTO members (user_id, last_active) VALUES (?, ?)",
                            (member.id, datetime.date.today().isoformat())
                        )
                        added_count += 1
        await db.commit()
    
    await interaction.followup.send(f"✅ База обновлена. Добавлено новых участников: {added_count}", ephemeral=True)

@bot.tree.command(name="setup", description="Настройка каналов и ролей (Только для Админа сервера)")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction, poll_channel: discord.TextChannel, log_channel: discord.TextChannel, admin_role: discord.Role, ignore_role: discord.Role):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("REPLACE INTO settings VALUES (?, ?, ?, ?, ?)", 
                         (interaction.guild.id, poll_channel.id, log_channel.id, admin_role.id, ignore_role.id))
        await db.commit()
    await interaction.response.send_message("✅ Настройки успешно сохранены!", ephemeral=True)

# Обработка ошибки доступа к setup
@setup.error
async def setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ Эту команду может выполнять только Администратор сервера.", ephemeral=True)

@bot.tree.command(name="opros_start", description="Запустить опрос (Только для Админа бота)")
@app_commands.describe(mode="Режим игры", date="Дата проведения")
async def opros_start(interaction: discord.Interaction, mode: Literal["tvt", "ltvt", "ttvt"], date: str):
    admin_role_id = await get_admin_role(interaction.guild.id)
    if not admin_role_id or not interaction.user.get_role(admin_role_id):
        return await interaction.response.send_message("❌ У вас нет прав для запуска опросов.", ephemeral=True)

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("INSERT INTO polls (guild_id, is_active) VALUES (?, 1)", (interaction.guild.id,))
        poll_num = cursor.lastrowid
        await db.commit()
        async with db.execute("SELECT poll_channel FROM settings WHERE guild_id = ?", (interaction.guild.id,)) as cursor:
            row = await cursor.fetchone()

    channel = interaction.guild.get_channel(row[0])
    unique_text = unique_messages.get(mode, "")
    
    content = (
        f"\n**⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯**\n"
        f"**Регистрация на {mode.upper()} #{poll_num}**\n"
        f"**Дата проведения:** {date}\n\n"
        f"Проголосовали: 0\n"
        f"✅ Иду: 0\n"
        f"❌ Не иду: 0\n"
        f"👨‍🦽 50/50: 0\n\n"
        f"@everyone {unique_text}"
    )

    msg = await channel.send(content=content, view=VoterView())
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE polls SET message_id = ? WHERE poll_id = ?", (msg.id, poll_num))
        await db.commit()

    await interaction.response.send_message(f"Опрос #{poll_num} запущен.", ephemeral=True)

# --- ФИНАЛЬНАЯ ВЕРСИЯ ROLL (НИКИ ВМЕСТО ID) ---
@bot.tree.command(name="roll", description="Выбрать случайного пользователя и показать список имен")
@app_commands.describe(users="Список участников через пробел или запятую (можно тегать @)")
async def roll(interaction: discord.Interaction, users: str):
    """Выбирает рандомного пользователя и преобразует теги в читаемые ники."""

    phrases = [
        "Самый удачливый сукин сын —",
        "Я выбираю тебя —",
        "Жребий пал на —",
        "Сегодня судьба благоволит —",
        "Звезды указали на —",
        "Фортуна выбрала именно тебя —"
    ]
    
    # 1. Очистка ввода
    clean_input = users.replace("[", "").replace("]", "").replace(",", " ")
    raw_user_list = [u.strip() for u in clean_input.split() if u.strip()]
    
    if not raw_user_list:
        return await interaction.response.send_message("❌ Список пуст!", ephemeral=True)
    
    # 2. Превращаем ID/Теги в читаемые ники для списка
    readable_names = []
    for user_item in raw_user_list:
        # Ищем ID внутри тега <@123456789> или <@!123456789>
        match = re.search(r'<@!?(\d+)>', user_item)
        if match:
            user_id = int(match.group(1))
            member = interaction.guild.get_member(user_id)
            if member:
                # Берем ник на сервере (display_name)
                readable_names.append(member.display_name)
            else:
                # Если пользователя нет на сервере, оставляем как есть
                readable_names.append(user_item)
        else:
            # Если это просто текст (не тег), оставляем как есть
            readable_names.append(user_item)

    # 3. Выбираем победителя из ИЗНАЧАЛЬНОГО списка (чтобы тег сработал для пинга)
    # Но для отображения в Embed выберем соответствующий ник
    winner_index = random.randrange(len(raw_user_list))
    raw_winner = raw_user_list[winner_index]
    winner_name = readable_names[winner_index]
    
    phrase = random.choice(phrases)
    participants_str = "\n".join(readable_names)
    
    # 4. Создаем Embed
    embed = discord.Embed(
        description=f"🎲 **Результаты ролла**\n\n{phrase} **{winner_name}**", 
        color=0x2ecc71
    )
    
    embed.set_footer(text=f"Список участников: \n{participants_str}")
    
    # В content отправляем raw_winner, чтобы прошел звуковой пинг, если это был тег
    await interaction.response.send_message(embed=embed)
    
@bot.tree.command(name="opros_stop", description="Остановить опрос (Только для Админа бота)")
async def opros_stop(interaction: discord.Interaction, number: int):
    admin_role_id = await get_admin_role(interaction.guild.id)
    if not admin_role_id or not interaction.user.get_role(admin_role_id):
        return await interaction.response.send_message("❌ У вас нет прав для остановки опросов.", ephemeral=True)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT message_id, is_active FROM polls WHERE poll_id = ?", (number,)) as cursor:
            poll = await cursor.fetchone()
        async with db.execute("SELECT poll_channel FROM settings WHERE guild_id = ?", (interaction.guild.id,)) as cursor:
            setts = await cursor.fetchone()

    if not poll or not poll[1]:
        return await interaction.response.send_message("Опрос не найден или уже закрыт.", ephemeral=True)

    try:
        channel = interaction.guild.get_channel(setts[0])
        message = await channel.fetch_message(poll[0])
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id, choice FROM votes WHERE message_id = ?", (message.id,)) as cursor:
                votes = await cursor.fetchall()
        
        categories = {"Иду": [], "Не иду": [], "50/50": []}
        for uid, choice in votes:
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else f"ID: {uid}"
            categories[choice].append(name)

        lines = message.content.split('\n')
        header = lines[2].replace("**Регистрация", "**[ЗАВЕРШЕН] Регистрация")
        date_line = lines[3]
        
        final_report = (
            f"\n**⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯**\n"
            f"[ЗАВЕРШЕНО] {header}\n"
            f"{date_line}\n\n"
            f"**ИТОГИ ГОЛОСОВАНИЯ:**\n"
            f"✅ **Иду ({len(categories['Иду'])}):** {', '.join(categories['Иду']) or '—'}\n"
            f"❌ **Не иду ({len(categories['Не иду'])}):** {', '.join(categories['Не иду']) or '—'}\n"
            f"👨‍🦽 **50/50 ({len(categories['50/50'])}):** {', '.join(categories['50/50']) or '—'}\n\n"
            f"Опрос закрыт администратором, ожидайте следующих игр"
        )

        await message.edit(content=final_report, view=None)
        
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE polls SET is_active = 0 WHERE poll_id = ?", (number,))
            await db.commit()
            
        await interaction.response.send_message(f"Опрос #{number} завершен.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("Ошибка: сообщение не найдено.", ephemeral=True)

@bot.tree.command(name="ignore_lists", description="Список прогульщиков (Только для Админа бота)")
async def ignore_lists(interaction: discord.Interaction):
    admin_role_id = await get_admin_role(interaction.guild.id)
    if not admin_role_id or not interaction.user.get_role(admin_role_id):
        return await interaction.response.send_message("Нет прав.", ephemeral=True)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, last_active FROM members") as cursor:
            rows = await cursor.fetchall()
    
    report = "**Список отсутствующих (30+ дней):**\n"
    found = False
    for u_id, last_active in rows:
        delta = (datetime.date.today() - datetime.date.fromisoformat(last_active)).days
        if delta >= 30:
            member = interaction.guild.get_member(u_id)
            name = member.display_name if member else f"ID: {u_id}"
            report += f"• {name} — {delta} дн.\n"
            found = True
    
    if not found: report = "Все участники активны!"
    await interaction.response.send_message(report, ephemeral=True)


bot.run(os.getenv('BOT_TOKEN'))