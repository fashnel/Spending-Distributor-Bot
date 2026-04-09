"""
Telegram-бот для распределения финансов из PDF-выписок.
Принимает PDF → парсит → агрегирует → записывает в Google Sheets.
"""

import io
import os
from datetime import datetime
import logging
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

apihelper.CONNECT_TIMEOUT = 300
apihelper.READ_TIMEOUT = 300
# ─── Настройка бота ──────────────────────────────────────────────────────────
apihelper.ENABLE_MIDDLEWARE = True
TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_ID = int(os.getenv("MY_ID"))
bot = telebot.TeleBot(TOKEN)


# ─── Фильтр: проверка ID пользователя ────────────────────────────────────────
class IsAllowedUser(telebot.custom_filters.SimpleCustomFilter):
    """Фильтр, пропускающий только разрешённого пользователя."""
    key = 'is_allowed'

    @staticmethod
    def check(message):
        if message.from_user.id != ALLOWED_USER_ID:
            return False
        return True


bot.add_custom_filter(IsAllowedUser())


# ─── Middleware: отправляем отказ неразрешённым пользователям ────────────────
# Хранит ID пользователей, которым уже отправили отказ, чтобы не спамить
_denied_users: set[int] = set()


@bot.middleware_handler(update_types=['message'])
def check_auth(bot_instance, message):
    if message.from_user.id != ALLOWED_USER_ID:
        if message.from_user.id not in _denied_users:
            try:
                bot_instance.send_message(
                    message.chat.id,
                    f"❌ Доступ закрыт (ID: {message.from_user.id}).",
                )
                _denied_users.add(message.from_user.id)
            except Exception as e:
                print(f"[AUTH] Не удалось отправить отказ: {e}")
        # Не возвращаем ничего — фильтр is_allowed на хендлерах заблокирует


# ─── Команда /start ──────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"], is_allowed=True)
def start(message):
    bot.reply_to(
        message,
        "👋 Привет! Отправь мне PDF из банка, "
        "и я запишу расходы в Google Sheets!",
    )


# ─── Обработка PDF-файлов ────────────────────────────────────────────────────
@bot.message_handler(content_types=["document", "text"], is_allowed=True)
def handle_pdf(message):
    """Скачивает PDF, парсит, агрегирует и записывает в таблицу."""
    # Проверка на случай текстовых сообщений
    if message.content_type != "document":
        return

    # Проверяем MIME-тип
    if not message.document or message.document.mime_type != "application/pdf":
        bot.reply_to(message, "📄 Я принимаю только PDF-файлы.")
        return

    print(f"\n[DEBUG] 1. Начинаю работу с файлом: {message.document.file_name}", flush=True)
    
    try:
        # Скачиваем файл
        print("[DEBUG] 2. Запрашиваю file_info у Telegram...", flush=True)
        file_info = bot.get_file(message.document.file_id)
        
        print(f"[DEBUG] 3. Начинаю скачивание файла ({message.document.file_size} байт)...", flush=True)
        downloaded_file = bot.download_file(file_info.file_path)
        print("[DEBUG] 4. Файл успешно скачан в память.", flush=True)

        # Сохраняем во временный файл
        pdf_buffer = io.BytesIO(downloaded_file)
        bot.reply_to(message, "⏳ PDF получен, начинаю обработку...")

        # 1. Парсим PDF
        print("[DEBUG] 6. Запускаю parse_pdf...", flush=True)
        raw_lines = parse_pdf(pdf_buffer)
        print(f"[DEBUG] 7. Парсинг завершен. Найдено строк: {len(raw_lines) if raw_lines else 0}", flush=True)

        # 2. Категоризируем и агрегируем
        print("[DEBUG] 8. Запускаю агрегацию категорий...", flush=True)
        expenses = categorize_and_aggregate(raw_lines)

        if not expenses:
            print("[DEBUG] ! Расходы не найдены после фильтрации.", flush=True)
            bot.send_message(message.chat.id, "⚠️ Не найдено расходов по заданным категориям.")
            return

        # 3. Формируем текстовый отчёт
        print("[DEBUG] 9. Формирую отчет для пользователя...", flush=True)
        report = "📊 <b>Найдено расходов:</b>\n\n"
        total = 0.0
        for date, category, amount in expenses:
            report += f"• {date} | {category} | {amount:,.2f}\n".replace(",", " ")
            total += amount
        report += f"\n💰 <b>Итого: {total:,.2f}</b>".replace(",", " ")

        bot.send_message(message.chat.id, report, parse_mode="html")

        # 4. Записываем в Google Sheets
        print("[DEBUG] 10. Подключаюсь к Google Sheets...", flush=True)
        worksheet, dates = get_or_create_worksheet(gc)
        print("[DEBUG] 11. Записываю данные в таблицу...", flush=True)
        added = append_expenses(worksheet, expenses, dates)

        bot.send_message(
            message.chat.id,
            f"✅ Успешно записано {added} строк в Google Таблицу.",
        )
        print("[DEBUG] 12. ВСЁ ГОТОВО! Успешное завершение.", flush=True)

    except Exception as e:
        print(f"[DEBUG] ❌ ОШИБКА: {str(e)}", flush=True)
        import traceback
        traceback.print_exc() # Выведет полную цепочку ошибки в логи
        bot.send_message(message.chat.id, f"❌ Ошибка при обработке: {e}")


# ─── Запуск бота ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("🤖 Бот запущен через стабильный polling...")
    while True:
        try:
            # Увеличиваем timeout здесь, чтобы дать время на тяжелые файлы
            bot.polling(none_stop=True, interval=1, timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"♻️ Рестарт из-за ошибки сети: {e}")
            import time
            time.sleep(5)
