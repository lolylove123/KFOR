import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import datetime
import re
import random
import asyncio
import os
import io
from dotenv import load_dotenv
from typing import Literal, Optional
from PIL import Image, ImageDraw, ImageFont
import aiohttp

load_dotenv()

# --- НАСТРОЙКИ БД ---
DB_NAME = "clan_base.db"

# --- УНИКАЛЬНЫЕ ТЕКСТЫ ---
unique_messages = {
    "tvt": "Прожмите,кто придет на игру, а кто нет. \nНе забудьте скачать моды заранее.",
    "ltvt": "Прожмите, кто придет на игру, а кто нет. \nНе забудьте скачать моды заранее.",
    "ttvt": "Прожмите, кто придет на игру, а кто нет. \nНе забудьте скачать моды заранее."
}

# --- ЛОГИКА ОПЫТА ---
def calculate_xp_next_level(level):
    return int(100 * (level ** 1.5))

async def add_xp(user_id: int, guild: discord.Guild, xp_to_add: int, channel: discord.abc.Messageable = None):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT xp, level FROM members WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row: return
            
            current_xp, current_lvl = row[0], row[1]
            new_xp = current_xp + xp_to_add
            xp_needed = calculate_xp_next_level(current_lvl)
            
            if new_xp >= xp_needed:
                current_lvl += 1
                new_xp -= xp_needed
                
                if channel:
                    await channel.send(f"🎊 Поздравляем <@{user_id}>! Ты поднял уровень до **{current_lvl}**!")
                else:
                    async with db.execute("SELECT active_log_channel FROM settings WHERE guild_id = ?", (guild.id,)) as s_cursor:
                        s_row = await s_cursor.fetchone()
                        if s_row:
                            target_chan = guild.get_channel(s_row[0])
                            if target_chan:
                                await target_chan.send(f"🎊 <@{user_id}> достиг **{current_lvl}** уровня!")

            await db.execute("UPDATE members SET xp = ?, level = ? WHERE user_id = ?", (new_xp, current_lvl, user_id))
            await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def has_required_role(member):
    role_names = [role.name for role in member.roles]
    return any(name in role_names for name in ["NATO", "NATOk"])

def draw_stats_card(data):
    name, lvl, xp, xp_need, kills, deaths, msgs, last_msg, last_vote, avatar_bytes = data
    
    try:
        base = Image.open('assets/bg.png').convert('RGBA')
    except:
        base = Image.new('RGBA', (900, 400), (20, 20, 25, 255))
    
    draw = ImageDraw.Draw(base)
    
    try:
        font_main = ImageFont.truetype("assets/font.ttf", 45)
        font_small = ImageFont.truetype("assets/font.ttf", 24)
    except:
        font_main = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Аватар (добавил проверку, если аватар вдруг не скачался)
    if avatar_bytes:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    else:
        # Серая заглушка, если discord не отдал аватар
        avatar = Image.new('RGBA', (160, 160), (100, 100, 100, 255)) 
        
    avatar = avatar.resize((160, 160))
    mask = Image.new('L', (160, 160), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 160, 160), fill=255)
    base.paste(avatar, (50, 50), mask)
    
    # Динамическое уменьшение шрифта для длинных ников
    max_name_width = 600
    temp_font_size = 45
    while draw.textlength(name, font=font_main) > max_name_width and temp_font_size > 20:
        temp_font_size -= 2
        try:
            font_main = ImageFont.truetype("assets/font.ttf", temp_font_size)
        except: break
    
    # Рисуем никнейм и уровень стандартным draw.text
    draw.text((240, 50), name, font=font_main, fill=(255, 255, 255))
    draw.text((240, 110), f"Уровень: {lvl}", font=font_small, fill=(46, 204, 113))

    # Прогресс бар
    bar_x, bar_y, bar_w, bar_h = 240, 150, 600, 35
    progress = max(0, min(xp / xp_need, 1.0)) if xp_need > 0 else 1.0
    draw.rounded_rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=10, fill=(40, 40, 45))
    if progress > 0:
        draw.rounded_rectangle([bar_x, bar_y, bar_x + (bar_w * progress), bar_y + bar_h], radius=10, fill=(46, 204, 113))
    
    draw.text((bar_x + 5, bar_y + 45), f"Опыт: {xp} / {xp_need}", font=font_small, fill=(200, 200, 200))

    # --- ЛОКАЛЬНЫЕ ИКОНКИ СТАТИСТИКИ ---
    stats_y = 260
    icon_size = (30, 30) # Единый размер для всех иконок
    
    # Вспомогательная функция, чтобы не дублировать код вставки картинок
    def draw_stat_with_icon(x, y, icon_path, text):
        try:
            icon = Image.open(icon_path).convert("RGBA").resize(icon_size)
            base.paste(icon, (x, y), icon)
            text_x = x + icon_size[0] + 10 # Сдвигаем текст вправо от иконки
        except Exception:
            # Если файла нет, просто рисуем текст без сдвига
            text_x = x 
            
        # Рисуем текст (немного опускаем его на +2 пикселя, чтобы отцентрировать с иконкой)
        draw.text((text_x, y + 2), text, font=font_small, fill=(255, 255, 255))

    # Вставляем иконки и текст
    draw_stat_with_icon(50, stats_y, "assets/icon_kills.png", f"Убийства: {kills}")
    draw_stat_with_icon(300, stats_y, "assets/icon_deaths.png", f"Смерти: {deaths}")
    draw_stat_with_icon(550, stats_y, "assets/icon_msgs.png", f"Сообщения: {msgs}")
    
    # Остальной текст (без иконок)
    draw.text((50, stats_y + 50), f"Последнее сообщение: {last_msg}", font=font_small, fill=(180, 180, 180))
    draw.text((50, stats_y + 85), f"Последнее голосование: {last_vote}", font=font_small, fill=(180, 180, 180))

    buffer = io.BytesIO()
    base.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

class VoterView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def update_activity(self, user_id):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO members (user_id, last_active, last_poll_vote) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET last_active = ?, last_poll_vote = ?",
                (user_id, datetime.date.today().isoformat(), now, datetime.date.today().isoformat(), now)
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
        return (
            f"\n**⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯**\n"
            f"**Регистрация на {mode.upper()} #{poll_num}**\n"
            f"**Дата проведения:** {date}\n\n"
            f"Проголосовали: {len(votes)}\n"
            f"✅ Иду: {results['Иду']}\n"
            f"❌ Не иду: {results['Не иду']}\n"
            f"👨‍🦽 50/50: {results['50/50']}\n\n"
            f"@everyone {unique_text}"
        )

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
            # 1. Проверяем, голосовал ли пользователь в этом опросе ранее
            async with db.execute(
                "SELECT 1 FROM votes WHERE message_id = ? AND user_id = ?", 
                (interaction.message.id, interaction.user.id)
            ) as cursor:
                already_voted = await cursor.fetchone()

            # 2. Сохраняем или обновляем голос
            await db.execute(
                "INSERT INTO votes (message_id, user_id, choice) VALUES (?, ?, ?) "
                "ON CONFLICT(message_id, user_id) DO UPDATE SET choice = ?",
                (interaction.message.id, interaction.user.id, choice, choice)
            )
            await db.commit()

            # 3. Получаем ID опроса для текста
            async with db.execute("SELECT poll_id FROM polls WHERE message_id = ?", (interaction.message.id,)) as cursor:
                poll_row = await cursor.fetchone()
        
        await self.update_activity(interaction.user.id)

        # 4. Начисляем XP только если это первый голос в этом опросе
        if not already_voted:
            await add_xp(interaction.user.id, interaction.guild, 50, channel=interaction.channel)
        
        # Обновление текста сообщения
        lines = interaction.message.content.strip().split('\n')
        try:
            header_line = [l for l in lines if "Регистрация на" in l][0]
            date_line = [l for l in lines if "Дата проведения:" in l][0]
            mode = header_line.split(' ')[2]
            poll_num = poll_row[0] if poll_row else "?"
            date = date_line.replace("**Дата проведения:** ", "")
            new_content = await self.get_poll_results_text(interaction.message.id, mode, date, poll_num)
            await interaction.response.edit_message(content=new_content)
        except Exception:
            # Если возникла ошибка при редактировании (например, взаимодействие истекло), 
            # используем follow-up, чтобы не ломать логику
            if not interaction.response.is_done():
                await interaction.response.send_message("Ошибка обновления текста опроса.", ephemeral=True)

class ClanBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.msg_cooldowns = {}

    async def setup_hook(self):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("""CREATE TABLE IF NOT EXISTS settings 
                (guild_id INTEGER PRIMARY KEY, opros_channel INTEGER, active_log_channel INTEGER, admin_role INTEGER, ignore_role INTEGER)""")
            
            try:
                await db.execute("ALTER TABLE settings ADD COLUMN stats_log_channel INTEGER")
            except aiosqlite.OperationalError: pass

            await db.execute("""CREATE TABLE IF NOT EXISTS members 
                (user_id INTEGER PRIMARY KEY, last_active TEXT)""")
            
            member_cols = [
                ("xp", "INTEGER DEFAULT 0"),
                ("level", "INTEGER DEFAULT 1"),
                ("messages_count", "INTEGER DEFAULT 0"),
                ("kills", "INTEGER DEFAULT 0"),
                ("deaths", "INTEGER DEFAULT 0"),
                ("last_message_time", "TEXT"),
                ("last_poll_vote", "TEXT")
            ]
            for col_name, col_type in member_cols:
                try:
                    await db.execute(f"ALTER TABLE members ADD COLUMN {col_name} {col_type}")
                except aiosqlite.OperationalError: pass

            await db.execute("""CREATE TABLE IF NOT EXISTS votes 
                (message_id INTEGER, user_id INTEGER, choice TEXT, PRIMARY KEY(message_id, user_id))""")
            await db.execute("""CREATE TABLE IF NOT EXISTS polls 
                (poll_id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER, guild_id INTEGER, is_active INTEGER)""")
            await db.commit()
            
        self.add_view(VoterView())
        self.check_activity.start()
        print("✅ База данных проверена и мигрирована.")

    async def on_ready(self):
        print(f'Бот {self.user} запущен!')
        await self.tree.sync()

    async def on_message(self, message):
        if message.author.bot or not message.guild: return
        
        user_id = message.author.id
        now = datetime.datetime.now()
        
        # Блок XP с анти-спамом (60 сек)
        last_xp = self.msg_cooldowns.get(user_id)
        if not last_xp or (now - last_xp).total_seconds() > 60:
            await add_xp(user_id, message.guild, random.randint(5, 15), channel=message.channel)
            self.msg_cooldowns[user_id] = now

        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute(
                "INSERT INTO members (user_id, last_active, messages_count, last_message_time) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET last_active = ?, messages_count = messages_count + 1, last_message_time = ?",
                (user_id, datetime.date.today().isoformat(), now.strftime("%Y-%m-%d %H:%M"), 
                 datetime.date.today().isoformat(), now.strftime("%Y-%m-%d %H:%M"))
            )
            await db.commit()

        await self.process_commands(message)

    async def on_member_join(self, member):
        if has_required_role(member):
            async with aiosqlite.connect(DB_NAME) as db:
                await db.execute("INSERT OR IGNORE INTO members (user_id, last_active) VALUES (?, ?)",
                    (member.id, datetime.date.today().isoformat()))
                await db.commit()

    async def on_member_remove(self, member):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM members WHERE user_id = ?", (member.id,))
            await db.commit()

    @tasks.loop(hours=24)
    async def check_activity(self):
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT guild_id, active_log_channel, ignore_role FROM settings") as cursor:
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
                    if not member or not has_required_role(member):
                        await db.execute("DELETE FROM members WHERE user_id = ?", (u_id,))
                        await db.commit()
                        continue
                    if ignore_id and member.get_role(ignore_id): continue
                    try:
                        last_date = datetime.date.fromisoformat(last_date_str)
                        delta = (datetime.date.today() - last_date).days
                        if delta >= 30 and delta % 30 == 0:
                            await channel.send(f"⚠️ **{member.display_name}** не активен {delta} дней!")
                    except: pass

bot = ClanBot()

async def get_admin_role(guild_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT admin_role FROM settings WHERE guild_id = ?", (guild_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

# --- КОМАНДЫ ---

@bot.tree.command(name="setup", description="Настройка каналов и ролей")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction, opros_channel: discord.TextChannel, active_log_channel: discord.TextChannel, stats_log_channel: discord.TextChannel, admin_role: discord.Role, ignore_role: discord.Role):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("REPLACE INTO settings (guild_id, opros_channel, active_log_channel, admin_role, ignore_role, stats_log_channel) VALUES (?, ?, ?, ?, ?, ?)", 
                         (interaction.guild.id, opros_channel.id, active_log_channel.id, admin_role.id, ignore_role.id, stats_log_channel.id))
        await db.commit()
    await interaction.response.send_message("✅ Настройки успешно сохранены!", ephemeral=True)

@bot.tree.command(name="kill_add", description="Добавить убийства пользователю")
async def kill_add(interaction: discord.Interaction, member: discord.Member, count: int):
    admin_id = await get_admin_role(interaction.guild.id)
    if not admin_id or not interaction.user.get_role(admin_id):
        return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE members SET kills = kills + ? WHERE user_id = ?", (count, member.id))
        await db.commit()
        
    if count > 0:
        await add_xp(member.id, interaction.guild, count * 200, channel=interaction.channel)

    await interaction.response.send_message(f"✅ {member.mention} начислено **{count}** убийств.")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT stats_log_channel FROM settings WHERE guild_id = ?", (interaction.guild.id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                chan = interaction.guild.get_channel(row[0])
                if chan: await chan.send(f"🛡️ Админ `{interaction.user}` изменил киллы `{member}` на `{count}`")

@bot.tree.command(name="death_add", description="Добавить смерти пользователю")
async def death_add(interaction: discord.Interaction, member: discord.Member, count: int):
    admin_id = await get_admin_role(interaction.guild.id)
    if not admin_id or not interaction.user.get_role(admin_id):
        return await interaction.response.send_message("❌ Нет прав.", ephemeral=True)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE members SET deaths = deaths + ? WHERE user_id = ?", (count, member.id))
        await db.commit()

    await interaction.response.send_message(f"✅ {member.mention} начислено **{count}** смертей.")
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT stats_log_channel FROM settings WHERE guild_id = ?", (interaction.guild.id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                chan = interaction.guild.get_channel(row[0])
                if chan: await chan.send(f"🛡️ Админ `{interaction.user}` изменил смерти `{member}` на `{count}`")

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

@bot.tree.command(name="stats", description="Показать статистику игрока")
async def stats(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    await interaction.response.defer()
    target = member or interaction.user
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT level, xp, kills, deaths, messages_count, last_message_time, last_poll_vote FROM members WHERE user_id = ?", (target.id,)) as cursor:
            row = await cursor.fetchone()
    
    if not row:
        return await interaction.followup.send("Пользователь не найден в базе.")

    lvl, xp, kills, deaths, msgs, last_msg, last_vote = row
    xp_need = calculate_xp_next_level(lvl)
    
    try:
        avatar_url = target.display_avatar.with_format("png").url
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url, timeout=10) as resp:
                avatar_bytes = await resp.read()
    except Exception:
        # Если аватар не загрузился, можно подставить пустые байты или дефолтную картинку
        avatar_bytes = None

    data = [target.display_name, lvl, xp, xp_need, kills, deaths, msgs, last_msg or "—", last_vote or "—", avatar_bytes]
    
    loop = asyncio.get_event_loop()
    
    try:
        # Устанавливаем жесткий лимит в 30 секунд на отрисовку
        result_buffer = await asyncio.wait_for(
            loop.run_in_executor(None, draw_stats_card, data), 
            timeout=30.0
        )
        
        file = discord.File(fp=result_buffer, filename="stats.png")
        await interaction.followup.send(file=file)
        
    except asyncio.TimeoutError:
        # Если рисование (или загрузка эмодзи внутри него) длилось дольше 30 сек
        await interaction.followup.send("Ошибка - повторите запрос снова (превышено время ожидания).")
    except Exception as e:
        # Любая другая критическая ошибка при отрисовке
        print(f"Ошибка в команде stats: {e}")
        await interaction.followup.send("Ошибка - повторите запрос снова.")

@bot.tree.command(name="opros_start", description="Запустить опрос")
@app_commands.describe(mode="Режим игры", date="Дата проведения")
async def opros_start(interaction: discord.Interaction, mode: Literal["tvt", "ltvt", "ttvt"], date: str):
    admin_role_id = await get_admin_role(interaction.guild.id)
    if not admin_role_id or not interaction.user.get_role(admin_role_id):
        return await interaction.response.send_message("❌ У вас нет прав.", ephemeral=True)

    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("INSERT INTO polls (guild_id, is_active) VALUES (?, 1)", (interaction.guild.id,))
        poll_num = cursor.lastrowid
        await db.commit()
        async with db.execute("SELECT opros_channel FROM settings WHERE guild_id = ?", (interaction.guild.id,)) as cursor:
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

@bot.tree.command(name="opros_stop", description="Остановить опрос (Только для Админа бота)")
async def opros_stop(interaction: discord.Interaction, number: int):
    admin_role_id = await get_admin_role(interaction.guild.id)
    if not admin_role_id or not interaction.user.get_role(admin_role_id):
        return await interaction.response.send_message("❌ У вас нет прав для остановки опросов.", ephemeral=True)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT message_id, is_active FROM polls WHERE poll_id = ?", (number,)) as cursor:
            poll = await cursor.fetchone()
        # ИСПРАВЛЕНО: столбец называется opros_channel, а не poll_channel
        async with db.execute("SELECT opros_channel FROM settings WHERE guild_id = ?", (interaction.guild.id,)) as cursor:
            setts = await cursor.fetchone()

    if not poll or not poll[1]:
        return await interaction.response.send_message("Опрос не найден или уже закрыт.", ephemeral=True)
    
    if not setts:
        return await interaction.response.send_message("Настройки бота не найдены. Выполните /setup.", ephemeral=True)

    try:
        channel = interaction.guild.get_channel(setts[0])
        message = await channel.fetch_message(poll[0])
        
        poll_date = "Дата не указана"
        for line in message.content.split('\n'):
            if "Дата проведения" in line:
                poll_date = line # Забираем всю строку целиком с оформлением
                break
        
        for line in message.content.split('\n'):
            if "Регистрация на" in line:
                poll_header = line # Забираем всю строку целиком с оформлением
                break

        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT user_id, choice FROM votes WHERE message_id = ?", (message.id,)) as cursor:
                votes = await cursor.fetchall()
        
        categories = {"Иду": [], "Не иду": [], "50/50": []}
        for uid, choice in votes:
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else f"ID: {uid}"
            categories[choice].append(name)

        final_report = (
            f"\n**⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯**\n"
            f"[ЗАВЕРШЕНА] {poll_header}\n"
            f"{poll_date}\n\n"
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
        print(f"Error in opros_stop: {e}")
        await interaction.response.send_message("Ошибка: сообщение опроса не найдено в канале.", ephemeral=True)

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
    today = datetime.date.today()

    for u_id, last_active in rows:
        if not last_active: continue
        try:
            # ИСПРАВЛЕНО: Берем только дату, если в поле вдруг записана дата + время
            clean_date_str = last_active.split(' ')[0]
            last_date = datetime.date.fromisoformat(clean_date_str)
            delta = (today - last_date).days
            
            if delta >= 30:
                member = interaction.guild.get_member(u_id)
                if member: # Проверяем, что юзер все еще на сервере
                    name = member.display_name
                    report += f"• {name} — {delta} дн.\n"
                    found = True
        except Exception as e:
            continue
    
    if not found: report = "Все участники активны!"
    await interaction.response.send_message(report, ephemeral=True)

bot.run(os.getenv('BOT_TOKEN'))