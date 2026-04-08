"""
Telegram-бот для распределения финансов из PDF-выписок.
Принимает PDF → парсит → агрегирует → записывает в Google Sheets.
"""

import os
from datetime import datetime

import telebot
from dotenv import load_dotenv
from telebot import apihelper

from google_sheets import (
    append_expenses,
    get_google_client,
    get_or_create_worksheet,
)
from pdf_processor import categorize_and_aggregate, parse_pdf

# ─── Загрузка переменных окружения ───────────────────────────────────────────
load_dotenv()

# ─── Настройка Google Sheets ─────────────────────────────────────────────────
gc = get_google_client()

# ─── Настройка бота ──────────────────────────────────────────────────────────
apihelper.ENABLE_MIDDLEWARE = True
TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("MY_ID"))

# Настройка прокси для Telegram API
PROXY_URL = os.getenv("PROXY_URL", "").strip()
if PROXY_URL:
    # Парсим URL прокси
    from urllib.parse import urlparse
    
    parsed = urlparse(PROXY_URL)
    proxy_type = parsed.scheme.lower()
    
    # Поддержка http, https, socks5, socks5h
    if proxy_type in ("socks5", "socks5h"):
        try:
            import socks
            apihelper.proxy = {
                "https": PROXY_URL,
                "http": PROXY_URL
            }
            print(f"✓ SOCKS5 прокси настроен: {parsed.hostname}:{parsed.port}")
        except ImportError:
            print("⚠ Для SOCKS5 прокси установите: pip install pysocks")
    elif proxy_type in ("http", "https"):
        apihelper.proxy = {
            "https": PROXY_URL,
            "http": PROXY_URL
        }
        print(f"✓ HTTP прокси настроен: {parsed.hostname}:{parsed.port}")
    else:
        print(f"⚠ Неизвестный тип прокси: {proxy_type}. Поддерживаются: http, https, socks5, socks5h")
else:
    print("ℹ Прокси не настроен, работа без прокси")

bot = telebot.TeleBot(TOKEN)


# ─── Middleware: проверка ID пользователя ────────────────────────────────────
@bot.middleware_handler(update_types=["message"])
def check_auth(bot_instance, message):
    """Пропускает сообщения только от разрешённого пользователя."""
    if message.from_user.id != ALLOWED_USER_ID:
        bot.send_message(
            message.chat.id,
            "⛔ У вас нет доступа к этому боту.",
        )
        raise telebot.apihelper.ApiException("Unauthorized")  # Останавливаем цепочку


# ─── Команда /start ──────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "👋 Привет! Отправь мне PDF-выписку из банка, "
        "и я запишу расходы в Google Таблицу.",
    )


# ─── Обработка PDF-файлов ────────────────────────────────────────────────────
@bot.message_handler(content_types=["document"])
def handle_pdf(message):
    """Скачивает PDF, парсит, агрегирует и записывает в таблицу."""
    # Проверяем MIME-тип
    if message.document.mime_type != "application/pdf":
        bot.reply_to(message, "📄 Я принимаю только PDF-файлы.")
        return

    # Скачиваем файл
    file_info = bot.get_file(message.document.file_id)
    downloaded_file = bot.download_file(file_info.file_path)

    # Сохраняем во временный файл
    temp_filename = f"statement_{message.from_user.id}_{int(datetime.now().timestamp())}.pdf"
    with open(temp_filename, "wb") as f:
        f.write(downloaded_file)

    bot.reply_to(message, "⏳ PDF получен, начинаю обработку...")

    try:
        # 1. Парсим PDF
        raw_lines = parse_pdf(temp_filename)

        # 2. Категоризируем и агрегируем
        expenses = categorize_and_aggregate(raw_lines)

        if not expenses:
            bot.send_message(message.chat.id, "⚠️ Не найдено расходов по заданным категориям.")
            return

        # 3. Формируем текстовый отчёт для пользователя
        report = "📊 <b>Найдено расходов:</b>\n\n"
        total = 0.0
        for date, category, amount in expenses:
            report += f"• {date} | {category} | {amount:,.2f}\n".replace(",", " ")
            total += amount
        report += f"\n💰 <b>Итого: {total:,.2f}</b>".replace(",", " ")

        bot.send_message(message.chat.id, report, parse_mode="html")

        # 4. Записываем в Google Sheets
        worksheet, dates = get_or_create_worksheet(gc)
        added = append_expenses(worksheet, expenses, dates)

        bot.send_message(
            message.chat.id,
            f"✅ Успешно записано {added} строк в Google Таблицу.",
        )

    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Ошибка при обработке: {e}")

    finally:
        # 5. Удаляем временный файл
        if os.path.exists(temp_filename):
            os.remove(temp_filename)


# ─── Запуск бота ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🤖 Бот запущен...")
    bot.infinity_polling()
