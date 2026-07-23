import discord
from discord.ext import tasks
import feedparser
import asyncio
import yt_dlp
from datetime import timedelta
import os
import json
import time
import random
from dotenv import load_dotenv

# ==========================================
# GÜVENLİ MİMARİ: .env DOSYASINI YÜKLE
# ==========================================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = discord.Bot(intents=intents)

# ==========================================
# VERİ DEPOLAMA YARDIMCILARI & KİLİT MEKANİZMASI
# ==========================================
SAYMA_VERI_DOSYASI = "sayma_verisi.json"
HABER_VERI_DOSYASI = "haber_kanallari.json"
RSS_KAYNAK_DOSYASI = "rss_kaynaklari.json"
KARSILAMA_VERI_DOSYASI = "karsilama_kanallari.json"
SEVIYE_VERI_DOSYASI = "seviye_verisi.json"
SEVIYE_ROL_DOSYASI = "seviye_rolleri.json"
MODUL_VERI_DOSYASI = "modul_ayarlari.json"

dosya_kilidi = asyncio.Lock()

def veriyi_oku(dosya_adi):
    if os.path.exists(dosya_adi):
        try:
            with open(dosya_adi, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def veriyi_kaydet(dosya_adi, veri):
    with open(dosya_adi, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=4)

async def veriyi_kaydet_async(dosya_adi, veri):
    async with dosya_kilidi:
        await asyncio.to_thread(veriyi_kaydet, dosya_adi, veri)

# Global Hafıza Yapıları
sayma_verileri = veriyi_oku(SAYMA_VERI_DOSYASI)
haber_kanallari = veriyi_oku(HABER_VERI_DOSYASI)
rss_kaynaklari = veriyi_oku(RSS_KAYNAK_DOSYASI) # { "guild_id": [ {"ad": "...", "url": "..."}, ... ] }
karsilama_kanallari = veriyi_oku(KARSILAMA_VERI_DOSYASI)
seviye_verileri = veriyi_oku(SEVIYE_VERI_DOSYASI)
seviye_rolleri = veriyi_oku(SEVIYE_ROL_DOSYASI)
modul_ayarlari = veriyi_oku(MODUL_VERI_DOSYASI)

muzik_hafizasi = {}
xp_cooldown = {} # { "guild_id_user_id": timestamp }

def get_muzik_veri(guild_id: int):
    if guild_id not in muzik_hafizasi:
        muzik_hafizasi[guild_id] = {"kuyruk": [], "su_an_calan": None}
    return muzik_hafizasi[guild_id]

def modul_aktif_mi(guild_id: int, modul_adi: str) -> bool:
    g_id = str(guild_id)
    return modul_ayarlari.get(g_id, {}).get(modul_adi, True)

# ==========================================
# 0. MODÜL AÇMA / KAPATMA SİSTEMİ
# ==========================================
@bot.slash_command(name="ayar-modul", description="Botun sistemlerini açıp kapatmanızı sağlar")
@discord.default_permissions(administrator=True)
async def ayar_modul(
    ctx: discord.ApplicationContext, 
    modul: discord.Option(str, "Değiştirmek istediğiniz modül", choices=["karsilama", "sayma", "rss", "muzik", "seviye", "moderasyon"]), # type: ignore
    durum: discord.Option(bool, "Açık (True) veya Kapalı (False)") # type: ignore
):
    await ctx.defer(ephemeral=True)

    if not ctx.author.guild_permissions.administrator:
        return await ctx.followup.send("❌ Bu komutu sadece `Yönetici` yetkisine sahip kişiler kullanabilir!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    if guild_id not in modul_ayarlari:
        modul_ayarlari[guild_id] = {}

    modul_ayarlari[guild_id][modul] = durum
    await veriyi_kaydet_async(MODUL_VERI_DOSYASI, modul_ayarlari)

    durum_metni = "🟢 **AÇIK**" if durum else "🔴 **KAPALI**"
    await ctx.followup.send(f"⚙️ **{modul.upper()}** modülü bu sunucu için {durum_metni} duruma getirildi.", ephemeral=True)

# ==========================================
# 1. HOŞ GELDİN (KARŞILAMA) SİSTEMİ
# ==========================================
@bot.slash_command(name="karsilama-kanali-ayarla", description="Sunucuya katılan yeni üyelerin karşılanacağı kanalı belirler")
@discord.default_permissions(manage_channels=True)
async def karsilama_kanali_ayarla(ctx: discord.ApplicationContext, kanal: discord.TextChannel):
    await ctx.defer(ephemeral=True)
    if not modul_aktif_mi(ctx.guild.id, "karsilama"):
        return await ctx.followup.send("❌ **Karşılama** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.manage_channels:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Kanalları Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    karsilama_kanallari[guild_id] = kanal.id
    await veriyi_kaydet_async(KARSILAMA_VERI_DOSYASI, karsilama_kanallari)

    await ctx.followup.send(f"🎉 Karşılama kanalı bu sunucu için {kanal.mention} olarak ayarlandı!", ephemeral=True)

@bot.event
async def on_member_join(member: discord.Member):
    if not modul_aktif_mi(member.guild.id, "karsilama"):
        return

    guild_id = str(member.guild.id)

    kanal_id = karsilama_kanallari.get(guild_id)
    if kanal_id:
        kanal = bot.get_channel(kanal_id)
        if kanal:
            embed_kanal = discord.Embed(
                title="👋 Sunucumuza Biri Katıldı!",
                description=(
                    f"Aramıza hoş geldin {member.mention}! 🎉\n\n"
                    f"Seninle birlikte **{member.guild.member_count}** kişi olduk!\n"
                    "Komutları görmek için `/yardim` yazabilirsin."
                ),
                color=discord.Color.green()
            )
            if member.display_avatar:
                embed_kanal.set_thumbnail(url=member.display_avatar.url)
            embed_kanal.set_footer(text=f"Kullanıcı ID: {member.id}")

            try:
                await kanal.send(content=f"Hoş geldin {member.mention}!", embed=embed_kanal)
            except discord.Forbidden:
                pass

    embed_dm = discord.Embed(
        title=f"🎉 Sunucumuza Hoş Geldin, {member.name}!",
        description=(
            f"Merhaba **{member.name}**, **{member.guild.name}** sunucusuna katıldığın için çok mutluyuz!\n\n"
            "🤖 Botumuzun tüm özelliklerini öğrenmek için sunucuda `/yardim` komutunu kullanabilirsin.\n\n"
            "🎵 Müzik dinlemek için bir ses kanalına girip `/oynat` yazabilir,\n"
            "🔢 Sayma oyununa katılabilir ve en güncel teknoloji haberlerini takip edebilirsin.\n\n"
            "Keyifli vakit geçirmeni dileriz! ✨"
        ),
        color=discord.Color.gold()
    )
    if member.display_avatar:
        embed_dm.set_thumbnail(url=member.display_avatar.url)
    embed_dm.set_footer(text=f"{member.guild.name} Yönetimi")

    try:
        await member.send(embed=embed_dm)
    except discord.Forbidden:
        pass

# ==========================================
# 2. SAYMA VE SEVİYE SİSTEMİ (ON_MESSAGE)
# ==========================================
@bot.slash_command(name="sayma-kanali-ayarla", description="Sayma oyununun oynanacağı kanalı belirler")
@discord.default_permissions(manage_channels=True)
async def sayma_kanali_ayarla(ctx: discord.ApplicationContext, kanal: discord.TextChannel):
    await ctx.defer(ephemeral=True)
    if not modul_aktif_mi(ctx.guild.id, "sayma"):
        return await ctx.followup.send("❌ **Sayma** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.manage_channels:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Kanalları Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    sayma_verileri[guild_id] = {
        "kanal_id": kanal.id,
        "mevcut_sayi": 0,
        "son_kullanici_id": None
    }
    await veriyi_kaydet_async(SAYMA_VERI_DOSYASI, sayma_verileri)

    await ctx.followup.send(f"🔢 Sayma kanalı {kanal.mention} olarak ayarlandı! Saymaya **1**'den başlayın!", ephemeral=True)

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    guild_id = str(message.guild.id)
    user_id = str(message.author.id)

    # --- A) SEVİYE & XP KAZANMA SİSTEMİ ---
    if modul_aktif_mi(message.guild.id, "seviye"):
        cooldown_key = f"{guild_id}_{user_id}"
        simdiki_zaman = time.time()

        if cooldown_key not in xp_cooldown or (simdiki_zaman - xp_cooldown[cooldown_key]) >= 60:
            xp_cooldown[cooldown_key] = simdiki_zaman

            if guild_id not in seviye_verileri:
                seviye_verileri[guild_id] = {}
            if user_id not in seviye_verileri[guild_id]:
                seviye_verileri[guild_id][user_id] = {"xp": 0, "level": 0}

            kullanici_veri = seviye_verileri[guild_id][user_id]
            kullanici_veri["xp"] += random.randint(15, 25)

            mevcut_seviye = kullanici_veri["level"]
            gereken_xp = (mevcut_seviye + 1) * 100

            if kullanici_veri["xp"] >= gereken_xp:
                kullanici_veri["level"] += 1
                yeni_seviye = kullanici_veri["level"]
                await veriyi_kaydet_async(SEVIYE_VERI_DOSYASI, seviye_verileri)

                await message.channel.send(f"🎉 Tebrikler {message.author.mention}! **Seviye {yeni_seviye}** seviyesine ulaştın!")

                guild_rolleri = seviye_rolleri.get(guild_id, {})
                if str(yeni_seviye) in guild_rolleri:
                    rol_id = guild_rolleri[str(yeni_seviye)]
                    rol = message.guild.get_role(rol_id)
                    if rol:
                        try:
                            await message.author.add_roles(rol)
                            await message.channel.send(f"🏅 **Ödül Rolü:** {message.author.mention}, **{rol.name}** rolünü kazandın!")
                        except discord.Forbidden:
                            pass

            await veriyi_kaydet_async(SEVIYE_VERI_DOSYASI, seviye_verileri)

    # --- B) SAYMA OYUNU SİSTEMİ ---
    if modul_aktif_mi(message.guild.id, "sayma"):
        guild_sayma = sayma_verileri.get(guild_id)

        if guild_sayma and guild_sayma.get("kanal_id") == message.channel.id:
            içerik = message.content.strip()

            if içerik.isdigit():
                girilen_sayi = int(içerik)
                beklenen_sayi = guild_sayma["mevcut_sayi"] + 1

                if message.author.id == guild_sayma.get("son_kullanici_id"):
                    await message.add_reaction("❌")
                    guild_sayma["mevcut_sayi"] = 0
                    guild_sayma["son_kullanici_id"] = None
                    await veriyi_kaydet_async(SAYMA_VERI_DOSYASI, sayma_verileri)
                    await message.channel.send(f"⚠️ **{message.author.mention}**, üst üste iki kez sayı yazamazsın! Sayma sıfırlandı. Tekrar **1** yazarak başlayın.")
                    return

                if girilen_sayi == beklenen_sayi:
                    await message.add_reaction("✅")
                    guild_sayma["mevcut_sayi"] = girilen_sayi
                    guild_sayma["son_kullanici_id"] = message.author.id
                    await veriyi_kaydet_async(SAYMA_VERI_DOSYASI, sayma_verileri)
                else:
                    await message.add_reaction("❌")
                    guild_sayma["mevcut_sayi"] = 0
                    guild_sayma["son_kullanici_id"] = None
                    await veriyi_kaydet_async(SAYMA_VERI_DOSYASI, sayma_verileri)
                    await message.channel.send(f"❌ **Yanlış sayı!** Beklenen sayı `{beklenen_sayi}` idi ama `{girilen_sayi}` yazıldı. Sayma sıfırlandı! Tekrar **1** ile başlayın.")

    await bot.process_commands(message)

# ==========================================
# 3. SEVİYE KOMUTLARI
# ==========================================
@bot.slash_command(name="seviye", description="Sizin veya başka bir üyenin seviyesini/XP durumunu gösterir")
async def seviye(ctx: discord.ApplicationContext, üye: discord.Member = None):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "seviye"):
        return await ctx.followup.send("❌ **Seviye** modülü bu sunucuda kapalı!", ephemeral=True)

    target = üye or ctx.author
    guild_id = str(ctx.guild.id)
    user_id = str(target.id)

    user_data = seviye_verileri.get(guild_id, {}).get(user_id, {"xp": 0, "level": 0})
    mevcut_xp = user_data["xp"]
    mevcut_seviye = user_data["level"]
    gereken_xp = (mevcut_seviye + 1) * 100

    embed = discord.Embed(title=f"📊 {target.display_name} - Seviye Bilgisi", color=discord.Color.blue())
    if target.display_avatar:
        embed.set_thumbnail(url=target.display_avatar.url)

    embed.add_field(name="🎖️ Seviye", value=f"**{mevcut_seviye}**", inline=True)
    embed.add_field(name="✨ Toplam XP", value=f"**{mevcut_xp}**", inline=True)
    embed.add_field(name="🎯 Sonraki Seviye İçin", value=f"`{mevcut_xp} / {gereken_xp} XP`", inline=False)

    await ctx.followup.send(embed=embed)

@bot.slash_command(name="seviye-rol-ekle", description="Belirli bir seviyeye ulaşıldığında verilecek ödül rolünü ayarlar")
@discord.default_permissions(manage_roles=True)
async def seviye_rol_ekle(ctx: discord.ApplicationContext, seviye: int, rol: discord.Role):
    await ctx.defer(ephemeral=True)
    if not modul_aktif_mi(ctx.guild.id, "seviye"):
        return await ctx.followup.send("❌ **Seviye** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.manage_roles:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Rolleri Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    if guild_id not in seviye_rolleri:
        seviye_rolleri[guild_id] = {}

    seviye_rolleri[guild_id][str(seviye)] = rol.id
    await veriyi_kaydet_async(SEVIYE_ROL_DOSYASI, seviye_rolleri)

    await ctx.followup.send(f"✅ **Seviye {seviye}** ödülü olarak {rol.mention} rolü başarıyla ayarlandı!", ephemeral=True)

@bot.slash_command(name="liderlik-tablosu", description="Sunucudaki en yüksek seviyeli 10 kişiyi gösterir")
async def liderlik_tablosu(ctx: discord.ApplicationContext):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "seviye"):
        return await ctx.followup.send("❌ **Seviye** modülü bu sunucuda kapalı!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    guild_data = seviye_verileri.get(guild_id, {})

    if not guild_data:
        return await ctx.followup.send("📜 Henüz bu sunucuda kimse XP kazanmadı.")

    sirali = sorted(guild_data.items(), key=lambda x: x[1]["xp"], reverse=True)[:10]

    description = ""
    for idx, (u_id, data) in enumerate(sirali, start=1):
        member = ctx.guild.get_member(int(u_id))
        isim = member.mention if member else f"Bilinmeyen Kullanıcı ({u_id})"
        description += f"**{idx}.** {isim} — **Seviye {data['level']}** *(XP: {data['xp']})*\n"

    embed = discord.Embed(title=f"🏆 {ctx.guild.name} - Seviye Liderlik Tablosu", description=description, color=discord.Color.gold())
    await ctx.followup.send(embed=embed)

# ==========================================
# 4. DİNAMİK RSS HABER SİSTEMİ
# ==========================================
gonderilen_haberler = []

@bot.slash_command(name="kanal-ayarla", description="RSS haberlerinin gönderileceği kanalı belirler")
@discord.default_permissions(manage_channels=True)
async def kanal_ayarla(ctx: discord.ApplicationContext, kanal: discord.TextChannel):
    await ctx.defer(ephemeral=True)
    if not modul_aktif_mi(ctx.guild.id, "rss"):
        return await ctx.followup.send("❌ **RSS Haber** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.manage_channels:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Kanalları Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    haber_kanallari[guild_id] = kanal.id
    await veriyi_kaydet_async(HABER_VERI_DOSYASI, haber_kanallari)

    await ctx.followup.send(f"✅ Haber kanalı bu sunucu için {kanal.mention} olarak ayarlandı!", ephemeral=True)

@bot.slash_command(name="rss-ekle", description="Sunucuya yeni bir RSS haber kaynağı ekler")
@discord.default_permissions(manage_channels=True)
async def rss_ekle(ctx: discord.ApplicationContext, isim: str, url: str):
    await ctx.defer(ephemeral=True)
    if not modul_aktif_mi(ctx.guild.id, "rss"):
        return await ctx.followup.send("❌ **RSS Haber** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.manage_channels:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Kanalları Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    if guild_id not in rss_kaynaklari:
        rss_kaynaklari[guild_id] = []

    # Geçerli bir RSS mi kontrol et
    feed = await asyncio.to_thread(feedparser.parse, url)
    if not feed.entries:
        return await ctx.followup.send("❌ Belirtilen URL geçerli bir RSS kaynağı gibi görünmüyor!", ephemeral=True)

    rss_kaynaklari[guild_id].append({"ad": isim, "url": url})
    await veriyi_kaydet_async(RSS_KAYNAK_DOSYASI, rss_kaynaklari)

    await ctx.followup.send(f"✅ **{isim}** adlı RSS kaynağı başarıyla eklendi!\n🔗 `{url}`", ephemeral=True)

@bot.slash_command(name="rss-sil", description="Sunucudaki bir RSS haber kaynağını siler")
@discord.default_permissions(manage_channels=True)
async def rss_sil(ctx: discord.ApplicationContext, isim: str):
    await ctx.defer(ephemeral=True)
    if not modul_aktif_mi(ctx.guild.id, "rss"):
        return await ctx.followup.send("❌ **RSS Haber** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.manage_channels:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Kanalları Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    liste = rss_kaynaklari.get(guild_id, [])

    yeni_liste = [k for k in liste if k["ad"].lower() != isim.lower()]

    if len(liste) == len(yeni_liste):
        return await ctx.followup.send(f"❌ **{isim}** adında bir RSS kaynağı bulunamadı.", ephemeral=True)

    rss_kaynaklari[guild_id] = yeni_liste
    await veriyi_kaydet_async(RSS_KAYNAK_DOSYASI, rss_kaynaklari)

    await ctx.followup.send(f"🗑️ **{isim}** adlı RSS kaynağı başarıyla kaldırıldı.", ephemeral=True)

@bot.slash_command(name="rss-liste", description="Sunucuya ekli RSS kaynaklarını listeler")
async def rss_liste(ctx: discord.ApplicationContext):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "rss"):
        return await ctx.followup.send("❌ **RSS Haber** modülü bu sunucuda kapalı!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    liste = rss_kaynaklari.get(guild_id, [])

    if not liste:
        return await ctx.followup.send("📜 Sunucuya eklenmiş hiçbir RSS kaynağı yok. `/rss-ekle` ile ekleyebilirsiniz.")

    embed = discord.Embed(title=f"🌐 {ctx.guild.name} - RSS Kaynakları", color=discord.Color.blue())
    metin = ""
    for idx, k in enumerate(liste, start=1):
        metin += f"**{idx}. {k['ad']}** — `<{k['url']}>` \n"

    embed.description = metin
    await ctx.followup.send(embed=embed)

@tasks.loop(minutes=5)
async def rss_kontrol():
    if not haber_kanallari:
        return

    for guild_id, kaynaklar in list(rss_kaynaklari.items()):
        if not modul_aktif_mi(int(guild_id), "rss"):
            continue

        kanal_id = haber_kanallari.get(guild_id)
        if not kanal_id:
            continue

        kanal = bot.get_channel(kanal_id)
        if not kanal:
            continue

        for kaynak in kaynaklar:
            try:
                feed = await asyncio.to_thread(feedparser.parse, kaynak["url"])
                if feed.entries:
                    son_haber = feed.entries[0]
                    haber_key = f"{guild_id}_{son_haber.link}"

                    if haber_key not in gonderilen_haberler:
                        gonderilen_haberler.append(haber_key)
                        if len(gonderilen_haberler) > 200:
                            gonderilen_haberler.pop(0)

                        embed = discord.Embed(
                            title=son_haber.title,
                            url=son_haber.link,
                            description=son_haber.get('summary', 'İçerik özeti yok.')[:200] + "...",
                            color=discord.Color.purple()
                        )
                        embed.set_author(name=f"🌐 {kaynak['ad']}")

                        try:
                            await kanal.send(embed=embed)
                        except discord.Forbidden:
                            pass
            except Exception as e:
                print(f"[RSS HATA] {kaynak['ad']} işlenirken hata: {e}")

# ==========================================
# 5. MÜZİK SİSTEMİ
# ==========================================
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_at_eof 1',
    'options': '-vn',
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

def sonraki_sarkiyi_cal(ctx):
    guild_id = ctx.guild.id
    m_veri = get_muzik_veri(guild_id)

    if len(m_veri["kuyruk"]) > 0:
        sonraki_sarki = m_veri["kuyruk"].pop(0)
        m_veri["su_an_calan"] = sonraki_sarki['title']

        source = discord.FFmpegPCMAudio(sonraki_sarki['url'], **FFMPEG_OPTIONS)
        ctx.voice_client.play(source, after=lambda e: sonraki_sarkiyi_cal(ctx))

        coro = ctx.channel.send(f"🎵 **Şimdi Çalıyor:** {sonraki_sarki['title']}")
        asyncio.run_coroutine_threadsafe(coro, bot.loop)
    else:
        m_veri["su_an_calan"] = None

async def ses_kanali_kontrol(ctx: discord.ApplicationContext) -> bool:
    if not modul_aktif_mi(ctx.guild.id, "muzik"):
        await ctx.followup.send("❌ **Müzik** modülü bu sunucuda kapalı!", ephemeral=True)
        return False

    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.followup.send("❌ Bu komutu kullanmak için bir ses kanalında olmalısınız!", ephemeral=True)
        return False

    if ctx.voice_client and ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.followup.send("❌ Bot ile aynı ses kanalında olmalısınız!", ephemeral=True)
        return False

    return True

@bot.slash_command(name="oynat", description="Şarkı çalar veya sıraya ekler")
async def oynat(ctx: discord.ApplicationContext, arama: str):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "muzik"):
        return await ctx.followup.send("❌ **Müzik** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.followup.send("❌ Önce bir ses kanalına katılmalısınız!", ephemeral=True)

    voice_channel = ctx.author.voice.channel
    voice_client = ctx.voice_client

    if not voice_client:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    m_veri = get_muzik_veri(ctx.guild.id)

    try:
        info = await asyncio.to_thread(lambda: ytdl.extract_info(f"ytsearch:{arama}", download=False))
        if 'entries' in info and len(info['entries']) > 0:
            sarki = info['entries'][0]
        else:
            return await ctx.followup.send("❌ Şarkı bulunamadı.")

        sarki_verisi = {'url': sarki['url'], 'title': sarki['title']}

        if voice_client.is_playing() or voice_client.is_paused():
            m_veri["kuyruk"].append(sarki_verisi)
            await ctx.followup.send(f"➕ **Sıraya Eklendi:** {sarki['title']} *(Sıradaki Pozisyon: {len(m_veri['kuyruk'])})*")
        else:
            m_veri["su_an_calan"] = sarki['title']
            source = discord.FFmpegPCMAudio(sarki['url'], **FFMPEG_OPTIONS)
            voice_client.play(source, after=lambda e: sonraki_sarkiyi_cal(ctx))
            await ctx.followup.send(f"🎵 **Şimdi Çalıyor:** {sarki['title']}")
    except Exception as e:
        await ctx.followup.send(f"❌ Şarkı işlenirken bir hata oluştu: {e}")

@bot.slash_command(name="duraklat", description="Çalan müziği duraklatır")
async def duraklat(ctx: discord.ApplicationContext):
    await ctx.defer()
    if not await ses_kanali_kontrol(ctx): return

    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.followup.send("⏸️ Müzik duraklatıldı.")
    else:
        await ctx.followup.send("❌ Şu an çalan bir müzik yok.")

@bot.slash_command(name="devam", description="Duraklatılan müziği devam ettirir")
async def devam(ctx: discord.ApplicationContext):
    await ctx.defer()
    if not await ses_kanali_kontrol(ctx): return

    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.followup.send("▶️ Müzik devam ettiriliyor.")
    else:
        await ctx.followup.send("❌ Duraklatılmış bir müzik yok.")

@bot.slash_command(name="atla", description="Sıradaki şarkıya geçer")
async def atla(ctx: discord.ApplicationContext):
    await ctx.defer()
    if not await ses_kanali_kontrol(ctx): return

    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.followup.send("⏭️ Şarkı atlandı!")
    else:
        await ctx.followup.send("❌ Atlanacak şarkı yok.")

@bot.slash_command(name="liste", description="Müzik kuyruğunu gösterir")
async def liste(ctx: discord.ApplicationContext):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "muzik"):
        return await ctx.followup.send("❌ **Müzik** modülü bu sunucuda kapalı!", ephemeral=True)

    m_veri = get_muzik_veri(ctx.guild.id)
    kuyruk = m_veri["kuyruk"]
    su_an_calan = m_veri["su_an_calan"]

    if not kuyruk and not su_an_calan:
        return await ctx.followup.send("📜 Müzik kuyruğu şu an boş.")

    embed = discord.Embed(title="🎶 Müzik Kuyruğu", color=discord.Color.blue())
    if su_an_calan:
        embed.add_field(name="🔊 Şu An Çalıyor", value=su_an_calan, inline=False)

    if kuyruk:
        liste_metni = ""
        for i, sarki in enumerate(kuyruk, start=1):
            liste_metni += f"**{i}.** {sarki['title']}\n"
        embed.add_field(name="⏳ Sıradakiler", value=liste_metni, inline=False)

    await ctx.followup.send(embed=embed)

@bot.slash_command(name="dur", description="Müziği durdurur ve kuyruğu temizler")
async def dur(ctx: discord.ApplicationContext):
    await ctx.defer()
    if not await ses_kanali_kontrol(ctx): return

    m_veri = get_muzik_veri(ctx.guild.id)
    m_veri["kuyruk"].clear()
    m_veri["su_an_calan"] = None

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.followup.send("🛑 Müzik durduruldu, kuyruk temizlendi ve kanaldan ayrılındı.")
    else:
        await ctx.followup.send("❌ Zaten bir ses kanalında değilim.")

# ==========================================
# 6. MODERASYON KOMUTLARI
# ==========================================
@bot.slash_command(name="clear", description="Belirtilen miktarda mesajı siler")
@discord.default_permissions(manage_messages=True)
async def clear(ctx: discord.ApplicationContext, miktar: int):
    await ctx.defer(ephemeral=True)
    if not modul_aktif_mi(ctx.guild.id, "moderasyon"):
        return await ctx.followup.send("❌ **Moderasyon** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.manage_messages:
        return await ctx.followup.send("❌ Bu komut için `Mesajları Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    if miktar < 1 or miktar > 100:
        return await ctx.followup.send("❌ Lütfen 1 ile 100 arasında bir sayı girin.", ephemeral=True)

    try:
        silinen = await ctx.channel.purge(limit=miktar)
        await ctx.followup.send(f"🧹 **{len(silinen)}** adet mesaj temizlendi.", ephemeral=True)
    except discord.Forbidden:
        await ctx.followup.send("❌ Botun bu kanalda `Mesajları Yönet` yetkisi yok!", ephemeral=True)
    except discord.HTTPException as e:
        await ctx.followup.send(f"❌ Mesajlar silinirken hata oluştu (14 günden eski mesajlar silinemez): {e}", ephemeral=True)

@bot.slash_command(name="kick", description="Kullanıcıyı sunucudan atar")
@discord.default_permissions(kick_members=True)
async def kick(ctx: discord.ApplicationContext, üye: discord.Member, sebep: str = "Belirtilmedi"):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "moderasyon"):
        return await ctx.followup.send("❌ **Moderasyon** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.kick_members:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Üyeleri At` yetkisine sahip olmalısınız!", ephemeral=True)

    if üye.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.followup.send("❌ Sizinle aynı veya sizden daha yüksek roldeki birini atamazsınız!", ephemeral=True)

    if üye.top_role >= ctx.guild.me.top_role:
        return await ctx.followup.send("❌ Bu kullanıcının rolü benim rolümden yüksek veya eşit, onu atamam!", ephemeral=True)

    try:
        await üye.kick(reason=sebep)
        await ctx.followup.send(f"👞 **{üye.mention}** sunucudan atıldı. *(Sebep: {sebep})*")
    except Exception as e:
        await ctx.followup.send(f"❌ Kullanıcı atılamadı: {e}")

@bot.slash_command(name="ban", description="Kullanıcıyı sunucudan yasaklar")
@discord.default_permissions(ban_members=True)
async def ban(ctx: discord.ApplicationContext, üye: discord.Member, sebep: str = "Belirtilmedi"):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "moderasyon"):
        return await ctx.followup.send("❌ **Moderasyon** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.ban_members:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Üyeleri Yasakla` yetkisine sahip olmalısınız!", ephemeral=True)

    if üye.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.followup.send("❌ Sizinle aynı veya sizden daha yüksek roldeki birini yasaklayamazsınız!", ephemeral=True)

    if üye.top_role >= ctx.guild.me.top_role:
        return await ctx.followup.send("❌ Bu kullanıcının rolü benim rolümden yüksek veya eşit, onu yasaklayamam!", ephemeral=True)

    try:
        await üye.ban(reason=sebep)
        await ctx.followup.send(f"🔨 **{üye.mention}** sunucudan yasaklandı. *(Sebep: {sebep})*")
    except Exception as e:
        await ctx.followup.send(f"❌ Kullanıcı yasaklanamadı: {e}")

@bot.slash_command(name="mute", description="Kullanıcıya süreli susturma (timeout) uygular")
@discord.default_permissions(moderate_members=True)
async def mute(ctx: discord.ApplicationContext, üye: discord.Member, dakika: int, sebep: str = "Belirtilmedi"):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "moderasyon"):
        return await ctx.followup.send("❌ **Moderasyon** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.moderate_members:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Üyeleri Zamana Aşımına Uğrat` yetkisine sahip olmalısınız!", ephemeral=True)

    if üye.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
        return await ctx.followup.send("❌ Sizinle aynı veya sizden daha yüksek roldeki birini susturamazsınız!", ephemeral=True)

    if üye.top_role >= ctx.guild.me.top_role:
        return await ctx.followup.send("❌ Bu kullanıcının rolü benim rolümden yüksek veya eşit, onu susturamam!", ephemeral=True)

    try:
        süre = timedelta(minutes=dakika)
        await üye.timeout_for(duration=süre, reason=sebep)
        await ctx.followup.send(f"🤫 **{üye.mention}**, {dakika} dakika boyunca susturuldu. *(Sebep: {sebep})*")
    except Exception as e:
        await ctx.followup.send(f"❌ Susturma uygulanamadı: {e}")

@bot.slash_command(name="unmute", description="Kullanıcının susturmasını kaldırır")
@discord.default_permissions(moderate_members=True)
async def unmute(ctx: discord.ApplicationContext, üye: discord.Member):
    await ctx.defer()
    if not modul_aktif_mi(ctx.guild.id, "moderasyon"):
        return await ctx.followup.send("❌ **Moderasyon** modülü bu sunucuda kapalı!", ephemeral=True)

    if not ctx.author.guild_permissions.moderate_members:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Üyeleri Zamana Aşımına Uğrat` yetkisine sahip olmalısınız!", ephemeral=True)

    try:
        await üye.remove_timeout()
        await ctx.followup.send(f"🔊 **{üye.mention}** kullanıcısının susturması kaldırıldı.")
    except Exception as e:
        await ctx.followup.send(f"❌ Susturma kaldırılamadı: {e}")

# ==========================================
# 7. YARDIM MENÜSÜ
# ==========================================
@bot.slash_command(name="yardim", description="Tüm komutları gösterir")
async def yardim(ctx: discord.ApplicationContext):
    await ctx.defer()
    embed = discord.Embed(
        title="🤖 Bot Yardım & Komut Rehberi",
        description="Aşağıda botun tüm özellikleri listelenmiştir.",
        color=discord.Color.gold()
    )

    embed.add_field(
        name="⚙️ Sistem Yönetimi",
        value="`/ayar-modul <modül_adı> <True/False>` - İstediğiniz sistemi sunucunuzda açar veya kapatır.",
        inline=False
    )

    embed.add_field(
        name="👋 Karşılama Sistemi",
        value="`/karsilama-kanali-ayarla #kanal` - Yeni gelen üyelerin karşılanacağı kanalı ayarlar.",
        inline=False
    )

    embed.add_field(
        name="🎖️ Seviye & Rank Sistemi",
        value=(
            "`/seviye [üye]` - Seviye ve XP durumunu gösterir.\n"
            "`/liderlik-tablosu` - En yüksek XP sahibi 10 kişiyi listeler.\n"
            "`/seviye-rol-ekle <seviye> <rol>` - Seviye ödülü belirler."
        ),
        inline=False
    )

    embed.add_field(
        name="🔢 Sayma (Counting) Oyunu",
        value="`/sayma-kanali-ayarla #kanal` - Sayma kanalını belirler.",
        inline=False
    )

    embed.add_field(
        name="📰 RSS Haber Ayarları",
        value=(
            "`/kanal-ayarla #kanal` - Haberlerin akacağı kanalı seçer.\n"
            "`/rss-ekle <isim> <url>` - Yeni bir haber kaynağı ekler.\n"
            "`/rss-sil <isim>` - Ekli olan bir kaynağı kaldırır.\n"
            "`/rss-liste` - Sunucudaki RSS kaynaklarını listeler."
        ),
        inline=False
    )

    embed.add_field(
        name="🎵 Müzik & Ses Kontrolleri",
        value=(
            "`/oynat <şarkı>` - Şarkı çalar veya sıraya ekler.\n"
            "`/duraklat` / `/devam` - Müziği dondurur/sürdürür.\n"
            "`/atla` - Sıradaki şarkıya geçer.\n"
            "`/liste` - Müzik kuyruğunu gösterir.\n"
            "`/dur` - Müziği kapatır ve kanaldan çıkar."
        ),
        inline=False
    )

    embed.add_field(
        name="🛡️ Moderasyon Komutları",
        value=(
            "`/clear <sayı>` - Belirtilen sayıda mesajı siler.\n"
            "`/kick <kullanıcı> [sebep]` - Üyeyi sunucudan atar.\n"
            "`/ban <kullanıcı> [sebep]` - Üyeyi yasaklar.\n"
            "`/mute <kullanıcı> <dakika> [sebep]` - Süreli susturma atar.\n"
            "`/unmute <kullanıcı>` - Susturmayı kaldırır."
        ),
        inline=False
    )

    await ctx.followup.send(embed=embed)

# ==========================================
# BOTU BAŞLATMA
# ==========================================
@bot.event
async def on_ready():
    print(f"✅ Bot {bot.user} olarak giriş yaptı!")
    if not rss_kontrol.is_running():
        rss_kontrol.start()
    await bot.change_presence(activity=discord.Game(name="/yardim | 7/24 Aktif"))

if __name__ == "__main__":
    if DISCORD_TOKEN:
        bot.run(DISCORD_TOKEN)
    else:
        print("❌ HATA: DISCORD_TOKEN bulunamadı! Lütfen environment değişkenlerini kontrol edin.")
