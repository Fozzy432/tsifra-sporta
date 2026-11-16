import asyncio
import json
import logging
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
    WebAppInfo,
)

# =========================================================================
# ВАШИ НАСТРОЙКИ (УЖЕ ЗАПОЛНЕНЫ)
# =========================================================================
BOT_TOKEN = "9111021275:AAFfcSkNLe1dkaBEqFG5hOGBWCGdL1etap4"  # Замените, если перевыпустили токен в @BotFather
ADMIN_ID = 6851997342
CHANNEL_USERNAME = "@DigitSportLive"
CHANNEL_URL = "https://t.me/DigitSportLive"
WEBAPP_URL = "https://fozzy432.github.io/tsifra-sporta/"
STARS_PRICE = 499

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Временное хранилище PRO-пользователей в памяти (можно заменить на SQLite)
paid_pro_users = {ADMIN_ID}


async def is_user_subscribed(user_id: int) -> bool:
    """Проверяет подписку пользователя на канал @DigitSportLive."""
    if user_id == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logging.error(f"Ошибка проверки подписки (убедитесь, что бот админ в {CHANNEL_USERNAME}): {e}")
        return False


def get_subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на @DigitSportLive", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Я подписался (Проверить)", callback_data="check_sub")],
        ]
    )


def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="📊 Открыть «Цифру Спорта»", web_app=WebAppInfo(url=WEBAPP_URL))],
    ]
    if user_id != ADMIN_ID and user_id not in paid_pro_users:
        buttons.append(
            [InlineKeyboardButton(text=f"⭐ Купить PRO-доступ ({STARS_PRICE} Stars)", callback_data="buy_pro_btn")]
        )
    else:
        buttons.append(
            [InlineKeyboardButton(text=f"⭐ Тест оплаты PRO ({STARS_PRICE} Stars)", callback_data="buy_pro_btn")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_stars_invoice(chat_id: int):
    """Отправляет официальный счёт на 499 Telegram Stars (валюта XTR)."""
    await bot.send_invoice(
        chat_id=chat_id,
        title="Цифра Спорта PRO (30 дней)",
        description="Автоматическая проверка экспрессов с помощью ИИ, поиск слабых событий и рекомендации по замене.",
        payload="pro_subscription_30d",
        provider_token="",  # Для Telegram Stars всегда пустая строка
        currency="XTR",     # Официальный код валюты Telegram Stars
        prices=[LabeledPrice(label="Подписка PRO на 1 месяц", amount=STARS_PRICE)],
    )


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    if not await is_user_subscribed(user_id):
        await message.answer(
            "🔒 **Доступ к «Цифре Спорта» открыт только подписчикам нашего канала!**\n\n"
            f"Подпишитесь на {CHANNEL_USERNAME}, а затем нажмите кнопку **«✅ Я подписался (Проверить)»** ниже.",
            parse_mode="Markdown",
            reply_markup=get_subscribe_keyboard(),
        )
        return

    # Если пользователь перешёл по кнопке «Оплатить 499 ⭐» из Mini App (?start=pay)
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip() == "pay":
        await send_stars_invoice(message.chat.id)
        return

    role_text = "👑 **Режим владельца (VIP PRO активен)**\n" if user_id == ADMIN_ID else ""
    await message.answer(
        f"👋 Добро пожаловать в **Цифру Спорта**!\n\n"
        f"{role_text}"
        "• Live-счёт и пересчёт вероятностей в реальном времени\n"
        "• Сравнение лучших коэффициентов БК\n"
        f"• ИИ-анализатор экспрессов (тариф PRO — {STARS_PRICE} ⭐/мес)",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(user_id),
    )


@dp.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_user_subscribed(user_id):
        await callback.answer("✅ Подписка подтверждена!", show_alert=False)
        await callback.message.edit_text(
            "✅ **Подписка на @DigitSportLive подтверждена!**\n\nНажмите кнопку ниже, чтобы открыть приложение:",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(user_id),
        )
    else:
        await callback.answer("❌ Вы ещё не подписались на @DigitSportLive!", show_alert=True)


@dp.callback_query(F.data == "buy_pro_btn")
async def cb_buy_pro(callback: types.CallbackQuery):
    await callback.answer()
    if not await is_user_subscribed(callback.from_user.id):
        await callback.message.answer(
            f"🔒 Сначала подпишитесь на канал {CHANNEL_USERNAME}:",
            reply_markup=get_subscribe_keyboard(),
        )
        return
    await send_stars_invoice(callback.message.chat.id)


@dp.message(Command("pro"))
@dp.message(Command("pay"))
async def cmd_pro(message: types.Message):
    if not await is_user_subscribed(message.from_user.id):
        await message.answer(
            f"🔒 Сначала подпишитесь на канал {CHANNEL_USERNAME}:",
            reply_markup=get_subscribe_keyboard(),
        )
        return
    await send_stars_invoice(message.chat.id)


@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        if data.get("action") == "buy_pro_stars":
            await send_stars_invoice(message.chat.id)
    except Exception as e:
        logging.error(f"Ошибка web_app_data: {e}")


# =========================================================================
# ОБЯЗАТЕЛЬНОЕ ПОДТВЕРЖДЕНИЕ ОПЛАТЫ TELEGRAM STARS
# =========================================================================
@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Подтверждаем Telegram, что готовы принять 499 звёзд
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    user_id = message.from_user.id
    paid_pro_users.add(user_id)
    stars_paid = message.successful_payment.total_amount

    # Передаём параметр ?pro=1 в ссылку кнопки, чтобы Mini App сразу активировало PRO
    pro_webapp_url = f"{WEBAPP_URL}?pro=1"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть PRO-доступ в приложении", web_app=WebAppInfo(url=pro_webapp_url))]
        ]
    )

    await message.answer(
        f"🎉 **Оплата {stars_paid} ⭐ прошла успешно!**\n\n"
        "Ваша подписка **Цифра Спорта PRO** активирована на 30 дней.\n"
        "Нажмите кнопку ниже, чтобы вернуться в приложение с открытым ИИ-анализатором:",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Бот @SportDigitBot запущен и готов принимать Telegram Stars...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
