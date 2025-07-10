# bot.py — Исправленный Telegram-бот Oplati Pay

import logging
from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes
)
from config import BOT_TOKEN, CARD_NUMBER, USD_MARKUP, ADMIN_USERNAME, ADMIN_CHAT_ID, LOG_FILE, RECEIPTS_DIR
from currency import get_usd_rate
from excel_utils import init_excel, save_order, get_stats
import datetime
import os

# Настройка состояний
WELCOME, COUNTRY_SERVICE, AMOUNT, RECEIPT = range(4)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация Excel
init_excel()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало взаимодействия с ботом"""
    user = update.effective_user
    context.user_data.clear()
    context.user_data.update({
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name
    })
    
    await update.message.reply_text(
        "👋 Привет! Я бот *Oplati Pay* для международных платежей.\n\n"
        "Пожалуйста, укажите:\n"
        "`Страна: [Название страны]`\n"
        "`Сервис: [Название сервиса]`\n\n"
        "Пример:\n`Страна: Германия`\n`Сервис: Netflix`",
        parse_mode="Markdown"
    )
    return COUNTRY_SERVICE

async def process_country_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода страны и сервиса"""
    text = update.message.text
    user_data = context.user_data
    
    # Парсинг ввода
    try:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        country = None
        service = None
        
        for line in lines:
            if line.lower().startswith("страна:"):
                country = line.split(":", 1)[1].strip()
            elif line.lower().startswith("сервис:"):
                service = line.split(":", 1)[1].strip()
        
        if not country or not service:
            raise ValueError("Неверный формат")
        
        # Простая проверка страны
        if not country.replace(" ", "").isalpha():
            await update.message.reply_text("❌ Пожалуйста, укажите корректное название страны.")
            return COUNTRY_SERVICE
        
        user_data["country"] = country
        user_data["service"] = service
        
        await update.message.reply_text(
            f"✅ Данные приняты:\n"
            f"🌍 Страна: *{country}*\n"
            f"🔧 Сервис: *{service}*\n\n"
            "💵 Теперь введите сумму оплаты в USD:",
            parse_mode="Markdown"
        )
        return AMOUNT
        
    except Exception as e:
        logger.error(f"Ошибка обработки страны/сервиса: {e}")
        await update.message.reply_text(
            "❌ Неверный формат. Пожалуйста, используйте:\n"
            "`Страна: [Название]`\n`Сервис: [Название]`",
            parse_mode="Markdown"
        )
        return COUNTRY_SERVICE

async def process_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка суммы платежа"""
    try:
        amount_usd = float(update.message.text.replace(",", "."))
        if amount_usd <= 0:
            raise ValueError("Сумма должна быть больше 0")
        
        # Получаем и рассчитываем курс
        base_rate = get_usd_rate()
        if base_rate is None:
            await update.message.reply_text("❌ Не удалось получить курс доллара. Попробуйте позже.")
            return ConversationHandler.END
        
        rate_with_markup = round(base_rate * (1 + USD_MARKUP), 2)
        amount_rub = round(amount_usd * rate_with_markup, 2)
        
        # Сохраняем данные
        context.user_data.update({
            "amount_usd": amount_usd,
            "amount_rub": amount_rub,
            "rate": rate_with_markup,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        })
        
        # Формируем сообщение с реквизитами
        payment_msg = (
            f"💳 *Реквизиты для оплаты:*\n\n"
            f"🔢 Номер карты: `{CARD_NUMBER}`\n"
            f"💵 Сумма к оплате: *{amount_usd:.2f} USD*\n"
            f"📈 Курс : *{rate_with_markup} ₽/USD*\n"
            f"💸 Итого: *{amount_rub:.2f} ₽*\n\n"
            "📤 После оплаты отправьте фото или PDF чека"
        )
        
        await update.message.reply_text(payment_msg, parse_mode="Markdown")
        
        # Логируем заказ
        save_order(context.user_data)
        
        return RECEIPT
        
    except ValueError:
        await update.message.reply_text("❌ Неверная сумма. Введите число больше 0 (например: 50.00)")
        return AMOUNT
    except Exception as e:
        logger.error(f"Ошибка обработки суммы: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова /start")
        return ConversationHandler.END

async def process_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка чека об оплате"""
    user = update.effective_user
    user_data = context.user_data
    
    if not user_data:
        await update.message.reply_text("❌ Сессия устарела. Начните снова /start")
        return ConversationHandler.END
    
    try:
        # Определяем тип файла
        if update.message.document:
            file = update.message.document
            ext = os.path.splitext(file.file_name)[1] or ".pdf"
        elif update.message.photo:
            file = update.message.photo[-1]
            ext = ".jpg"
        else:
            await update.message.reply_text("❌ Пожалуйста, отправьте PDF или фото чека")
            return RECEIPT
        
        # Сохраняем файл
        receipt_path = os.path.join(RECEIPTS_DIR, f"receipt_{user.id}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}{ext}")
        receipt_file = await file.get_file()
        await receipt_file.download_to_drive(receipt_path)
        
        # Формируем сообщение для админа
        admin_msg = (
            f"📥 *Новый платеж!*\n\n"
            f"👤 Пользователь: @{user.username or user.first_name}\n"
            f"🆔 ID: `{user.id}`\n"
            f"🌍 Страна: *{user_data['country']}*\n"
            f"🔧 Сервис: *{user_data['service']}*\n"
            f"💵 Сумма: *{user_data['amount_usd']:.2f} USD*\n"
            f"💸 Сумма RUB: *{user_data['amount_rub']:.2f} ₽*\n"
            f"📅 Дата: {user_data['date']}"
        )
        
        # Отправляем админу
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_msg,
            parse_mode="Markdown"
        )
        
        # Отправляем файл админу
        if update.message.document:
            await context.bot.send_document(
                chat_id=ADMIN_CHAT_ID,
                document=file.file_id,
                caption=f"Чек от @{user.username}"
            )
        else:
            await context.bot.send_photo(
                chat_id=ADMIN_CHAT_ID,
                photo=file.file_id,
                caption=f"Чек от @{user.username}"
            )
        
        # Обновляем статус в логе
        save_order(user_data, status="Оплачено")
        
        # Финал для пользователя
        await update.message.reply_text(
            "✅ Чек получен! Менеджер @ShipovM свяжется с вами в течение 10 минут.\n"
            "Для нового платежа отправьте /start"
        )
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Ошибка обработки чека: {e}")
        await update.message.reply_text("❌ Произошла ошибка при обработке чека. Попробуйте отправить снова.")
        return RECEIPT

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику платежей"""
    total = get_stats()
    await update.message.reply_text(
        f"📊 *Статистика платежей*\n\n"
        f"Всего оплачено: *{total:.2f} ₽*",
        parse_mode="Markdown"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена операции"""
    await update.message.reply_text("❌ Операция отменена")
    return ConversationHandler.END

def main() -> None:
    """Запуск бота"""
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Обработчик диалога
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            COUNTRY_SERVICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_country_service)
            ],
            AMOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_amount)
            ],
            RECEIPT: [
                MessageHandler(filters.PHOTO | filters.Document.PDF, process_receipt),
                MessageHandler(filters.ALL, 
                    lambda update, _: update.message.reply_text("❌ Отправьте PDF или фото чека"))
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("stats", stats))
    
    application.run_polling()

if __name__ == "__main__":
    main()