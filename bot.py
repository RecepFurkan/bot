import discord
from discord.ext import tasks
import feedparser
import asyncio
import yt_dlp
from datetime import timedelta
import os
import json
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
KARSILAMA_VERI_DOSYASI = "karsilama_kanallari.json"

# Eşzamanlı dosya yazma çakışmalarını önlemek için kilit
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
    # Race-condition engellemek için async kilit
    async with dosya_kilidi:
        await asyncio.to_thread(veriyi_kaydet, dosya_adi, veri)

# Global Hafıza Yapıları
sayma_verileri = veriyi_oku(SAYMA_VERI_DOSYASI)
haber_kanallari = veriyi_oku(HABER_VERI_DOSYASI)
karsilama_kanallari = veriyi_oku(KARSILAMA_VERI_DOSYASI)

muzik_hafizasi = {}

def get_muzik_veri(guild_id: int):
    if guild_id not in muzik_hafizasi:
        muzik_hafizasi[guild_id] = {"kuyruk": [], "su_an_calan": None}
    return muzik_hafizasi[guild_id]

# ==========================================
# 1. HOŞ GELDİN (KARŞILAMA) SİSTEMİ
# ==========================================
@bot.slash_command(name="karsilama-kanali-ayarla", description="Sunucuya katılan yeni üyelerin karşılanacağı kanalı belirler")
@discord.default_permissions(manage_channels=True)
async def karsilama_kanali_ayarla(ctx: discord.ApplicationContext, kanal: discord.TextChannel):
    await ctx.defer(ephemeral=True)
    
    # Kod içi yetki kontrolü
    if not ctx.author.guild_permissions.manage_channels:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Kanalları Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    karsilama_kanallari[guild_id] = kanal.id
    await veriyi_kaydet_async(KARSILAMA_VERI_DOSYASI, karsilama_kanallari)

    await ctx.followup.send(f"🎉 Karşılama kanalı bu sunucu için {kanal.mention} olarak ayarlandı!", ephemeral=True)

@bot.event
async def on_member_join(member: discord.Member):
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
                print(f"[KARŞILAMA HATA] {kanal.name} kanalına mesaj gönderilemedi (Yetki eksik).")

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
# 2. SAYMA (COUNTING) SİSTEMİ
# ==========================================
@bot.slash_command(name="sayma-kanali-ayarla", description="Sayma oyununun oynanacağı kanalı belirler")
@discord.default_permissions(manage_channels=True)
async def sayma_kanali_ayarla(ctx: discord.ApplicationContext, kanal: discord.TextChannel):
    await ctx.defer(ephemeral=True)
    
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
# 3. RSS HABER SİSTEMİ
# ==========================================
HABER_KAYNAKLARI = [
    {"ad": "The Verge", "url": "https://www.theverge.com/rss/index.xml", "renk": discord.Color.purple()},
    {"ad": "TechCrunch", "url": "https://techcrunch.com/feed/", "renk": discord.Color.green()},
    {"ad": "Ars Technica", "url": "http://feeds.arstechnica.com/arstechnica/index", "renk": discord.Color.orange()},
    {"ad": "Wired", "url": "https://www.wired.com/feed/rss", "renk": discord.Color.blue()},
    {"ad": "Hacker News", "url": "https://news.ycombinator.com/rss", "renk": discord.Color.dark_orange()}
]

gonderilen_haberler = []

@bot.slash_command(name="kanal-ayarla", description="RSS haberlerinin gönderileceği kanalı belirler")
@discord.default_permissions(manage_channels=True)
async def kanal_ayarla(ctx: discord.ApplicationContext, kanal: discord.TextChannel):
    await ctx.defer(ephemeral=True)
    
    if not ctx.author.guild_permissions.manage_channels:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Kanalları Yönet` yetkisine sahip olmalısınız!", ephemeral=True)

    guild_id = str(ctx.guild.id)
    haber_kanallari[guild_id] = kanal.id
    await veriyi_kaydet_async(HABER_VERI_DOSYASI, haber_kanallari)

    await ctx.followup.send(f"✅ Haber kanalı bu sunucu için {kanal.mention} olarak ayarlandı!", ephemeral=True)

@tasks.loop(minutes=5)
async def rss_kontrol():
    if not haber_kanallari:
        return

    for kaynak in HABER_KAYNAKLARI:
        try:
            feed = await asyncio.to_thread(feedparser.parse, kaynak["url"])
            if feed.entries:
                son_haber = feed.entries[0]
                if son_haber.link not in gonderilen_haberler:
                    gonderilen_haberler.append(son_haber.link)
                    if len(gonderilen_haberler) > 100:
                        gonderilen_haberler.pop(0)

                    embed = discord.Embed(
                        title=son_haber.title,
                        url=son_haber.link,
                        description=son_haber.get('summary', 'İçerik özeti yok.')[:200] + "...",
                        color=kaynak["renk"]
                    )
                    embed.set_author(name=f"🌐 {kaynak['ad']}")

                    for guild_id, kanal_id in list(haber_kanallari.items()):
                        kanal = bot.get_channel(kanal_id)
                        if kanal:
                            try:
                                await kanal.send(embed=embed)
                            except discord.Forbidden:
                                pass
        except Exception as e:
            print(f"[RSS HATA] {kaynak['ad']} işlenirken hata: {e}")

# ==========================================
# 4. MÜZİK SİSTEMİ (SES KANALI GÜVENLİĞİ EKLENDİ)
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
    """Kullanıcının ve botun ses kanalı durumlarını denetler."""
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
    
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.followup.send("❌ Önce bir ses kanalına katılmalısınız!", ephemeral=True)

    voice_channel = ctx.author.voice.channel
    voice_client = ctx.voice_client

    # Ses kanalına bağlanma / kanal değiştirme kontrolü
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
# 5. MODERASYON KOMUTLARI (HİYERARŞİ VE YETKİ KONTROLÜ)
# ==========================================
@bot.slash_command(name="clear", description="Belirtilen miktarda mesajı siler")
@discord.default_permissions(manage_messages=True)
async def clear(ctx: discord.ApplicationContext, miktar: int):
    await ctx.defer(ephemeral=True)

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

    if not ctx.author.guild_permissions.kick_members:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Üyeleri At` yetkisine sahip olmalısınız!", ephemeral=True)

    # Rol Hiyerarşisi Kontrolü
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

    if not ctx.author.guild_permissions.ban_members:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Üyeleri Yasakla` yetkisine sahip olmalısınız!", ephemeral=True)

    # Rol Hiyerarşisi Kontrolü
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

    if not ctx.author.guild_permissions.moderate_members:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Üyeleri Zamana Aşımına Uğrat` yetkisine sahip olmalısınız!", ephemeral=True)

    # Rol Hiyerarşisi Kontrolü
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

    if not ctx.author.guild_permissions.moderate_members:
        return await ctx.followup.send("❌ Bu komutu kullanmak için `Üyeleri Zamana Aşımına Uğrat` yetkisine sahip olmalısınız!", ephemeral=True)

    try:
        await üye.remove_timeout()
        await ctx.followup.send(f"🔊 **{üye.mention}** kullanıcısının susturması kaldırıldı.")
    except Exception as e:
        await ctx.followup.send(f"❌ Susturma kaldırılamadı: {e}")

# ==========================================
# 6. YARDIM MENÜSÜ
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
        name="👋 Karşılama Sistemi",
        value="`/karsilama-kanali-ayarla #kanal` - Yeni gelen üyelerin karşılanacağı kanalı ayarlar.",
        inline=False
    )

    embed.add_field(
        name="🔢 Sayma (Counting) Oyunu",
        value="`/sayma-kanali-ayarla #kanal` - Sayma kanalını belirler.",
        inline=False
    )

    embed.add_field(
        name="📰 RSS Haber Ayarları",
        value="`/kanal-ayarla #kanal` - Haberlerin akacağı kanalı seçer.",
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
# BOTU BAŞLATMA & OTM. OYNUYOR DURUMU
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
