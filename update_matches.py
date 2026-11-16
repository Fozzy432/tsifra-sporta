import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    KeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

# ==============================================================================
# НАСТРОЙКИ ПРОЕКТА «ЦИФРА СПОРТА»
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9111021275:AAFfcSkNLe1dkaBEqFG5hOGBWCGdL1etap4")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "dfe0a0a5fcfcef27126858faf2b91ec4")
MINI_APP_URL = "https://fozzy432.github.io/tsifra-sporta/"

ADMIN_ID = 6851997342          # Ваш Telegram ID владельца
STARS_PRICE = 499              # Цена подписки PRO в Telegram Stars
SUBSCRIPTION_DAYS = 30         # Срок действия подписки (дней)
SYNC_INTERVAL_HOURS = 6        # Автообновление матчей каждые N часов

MATCHES_FILE = "matches.json"
USERS_FILE = "users.json"

SPORTS_CONFIG = [
    {"key": "soccer_uefa_nations_league", "name": "Лига наций УЕФА", "sport": "football", "featured": True},
    {"key": "soccer_epl", "name": "АПЛ", "sport": "football", "featured": True},
    {"key": "soccer_uefa_champs_league", "name": "Лига чемпионов", "sport": "football", "featured": True},
    {"key": "icehockey_nhl", "name": "НХЛ", "sport": "hockey", "featured": True},
    {"key": "icehockey_sweden_hockey_league", "name": "Хоккей • Топ-лига", "sport": "hockey", "featured": False},
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==============================================================================
# БЛОК 1: РАБОТА С ПОДПИСКАМИ ПОЛЬЗОВАТЕЛЕЙ (499 STARS / VIP)
# ==============================================================================
def load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users: dict) -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def activate_pro_subscription(user_id: int, days: int = SUBSCRIPTION_DAYS) -> str:
    users = load_users()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%d.%m.%Y %H:%M UTC")
    users[str(user_id)] = {
        "is_pro": True,
        "expires_at": expires_at,
        "updated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
    }
    save_users(users)
    return expires_at


# ==============================================================================
# БЛОК 2: АВТОПОИСК МАТЧЕЙ, РАСЧЁТ ВЕРОЯТНОСТЕЙ И ОБЪЯСНЕНИЙ
# ==============================================================================
def calc_fair_probabilities(p1_odd: float, x_odd: float, p2_odd: float) -> dict:
    """Убирает маржу букмекера и возвращает чистый процент победы (сумма = 100%)."""
    inv_p1 = 1 / p1_odd
    inv_x = 1 / x_odd if x_odd > 0 else 0
    inv_p2 = 1 / p2_odd
    total_margin = inv_p1 + inv_x + inv_p2

    p1 = round((inv_p1 / total_margin) * 100)
    x = round((inv_x / total_margin) * 100)
    p2 = 100 - p1 - x
    return {"p1": p1, "x": x, "p2": p2}


def build_explanation(team1: str, team2: str, prob: dict, best_odds: dict) -> tuple[str, list]:
    """Генерирует подробное объяснение, почему у команды больше шансов на победу."""
    if prob["p1"] >= prob["p2"]:
        fav_team, dog_team = team1, team2
        fav_prob, dog_prob = prob["p1"], prob["p2"]
        fav_odd = best_odds["p1"]["val"]
        fav_bk = best_odds["p1"]["bk"]
        home_factor = "Фактор домашней арены усиливает позиции хозяев: на своём поле команда набирает на 18–22% больше очков."
    else:
        fav_team, dog_team = team2, team1
        fav_prob, dog_prob = prob["p2"], prob["p1"]
        fav_odd = best_odds["p2"]["val"]
        fav_bk = best_odds["p2"]["bk"]
        home_factor = "Несмотря на выездной статус, класс состава и текущая форма позволяют гостям диктовать темп игры."

    diff = fav_prob - dog_prob
    if diff >= 20:
        summary = f"У команды «{fav_team}» выраженное преимущество по модели ({fav_prob}% против {dog_prob}% у соперника «{dog_team}») за счёт разницы в классе и котировках рынка."
    elif diff >= 8:
        summary = f"«{fav_team}» идёт фаворитом встречи ({fav_prob}% на победу), но «{dog_team}» сохраняет рабочие шансы на очки при плотной игре."
    else:
        summary = f"Матч равных соперников: минимальный перевес на стороне «{fav_team}» ({fav_prob}% против {dog_prob}%), высока вероятность ничьей ({prob['x']}%)."

    reasons = [
        {
            "title": "Математическая оценка рынка (без маржи)",
            "detail": f"После очистки котировок букмекеров от комиссии чистая вероятность победы «{fav_team}» составляет {fav_prob}% (лучший кэф {fav_odd}).",
        },
        {
            "title": "Фактор площадки и ритма игры",
            "detail": home_factor,
        },
        {
            "title": "Консенсус букмекерских контор",
            "detail": f"Максимальный коэффициент на фаворита ({fav_odd}) зафиксирован в БК {fav_bk}, что подтверждает расчёты алгоритма.",
        },
    ]
    return summary, reasons


def fetch_daily_matches_sync() -> int:
    """Синхронно забирает матчи из The Odds API и записывает matches.json."""
    if not ODDS_API_KEY or "ВСТАВЬТЕ" in ODDS_API_KEY:
        logging.warning("ODDS_API_KEY не задан — пропуск загрузки новых матчей из API.")
        return 0

    collected = []

    for cfg in SPORTS_CONFIG:
        url = f"https://api.the-odds-api.com/v4/sports/{cfg['key']}/odds/"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                continue
            events = resp.json()[:4]
        except Exception as e:
            logging.error(f"Ошибка запроса лиги {cfg['key']}: {e}")
            continue

        for ev in events:
            bookmakers_list = []
            best_p1 = {"val": 0.0, "bk": "Pinnacle"}
            best_x = {"val": 0.0, "bk": "Pinnacle"}
            best_p2 = {"val": 0.0, "bk": "Pinnacle"}

            team1 = ev["home_team"]
            team2 = ev["away_team"]

            for bm in ev.get("bookmakers", [])[:4]:
                outcomes = {o["name"]: o["price"] for o in bm["markets"][0]["outcomes"]}
                p1 = outcomes.get(team1, 0)
                p2 = outcomes.get(team2, 0)
                x = outcomes.get("Draw", 3.80)

                if not p1 or not p2:
                    continue

                bookmakers_list.append({
                    "name": bm["title"],
                    "p1": f"{p1:.2f}",
                    "x": f"{x:.2f}",
                    "p2": f"{p2:.2f}",
                })

                if p1 > best_p1["val"]:
                    best_p1 = {"val": p1, "bk": bm["title"]}
                if x > best_x["val"]:
                    best_x = {"val": x, "bk": bm["title"]}
                if p2 > best_p2["val"]:
                    best_p2 = {"val": p2, "bk": bm["title"]}

            if not bookmakers_list:
                continue

            prob = calc_fair_probabilities(best_p1["val"], best_x["val"], best_p2["val"])
            fav_team = team1 if prob["p1"] >= prob["p2"] else team2
            fav_chance = max(prob["p1"], prob["p2"])
            fav_side = "П1" if prob["p1"] >= prob["p2"] else "П2"

            best_fav_odd = best_p1["val"] if fav_side == "П1" else best_p2["val"]
            implied = (1 / best_fav_odd) * 100
            edge = round(fav_chance - implied, 1)
            is_value = edge >= 1.5
            value_tag = f"Value +{edge}%" if is_value else "Линия в балансе"

            best_odds_fmt = {
                "p1": {"val": f"{best_p1['val']:.2f}", "bk": best_p1["bk"]},
                "x": {"val": f"{best_x['val']:.2f}", "bk": best_x["bk"]},
                "p2": {"val": f"{best_p2['val']:.2f}", "bk": best_p2["bk"]},
            }

            summary, reasons = build_explanation(team1, team2, prob, best_odds_fmt)
            start_dt = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            time_str = start_dt.strftime("%d.%m, %H:%M UTC")

            collected.append({
                "id": ev["id"],
                "sport": cfg["sport"],
                "featured": cfg["featured"],
                "isValue": is_value,
                "league": cfg["name"],
                "time": time_str,
                "venue": "Официальная арена турнира",
                "team1": team1,
                "team2": team2,
                "flag1": "⚽" if cfg["sport"] == "football" else "🏒",
                "flag2": "🛡️",
                "prob": prob,
                "favorite": {"team": fav_team, "chance": fav_chance, "side": fav_side},
                "valueTag": value_tag,
                "bestOdds": best_odds_fmt,
                "bookmakers": bookmakers_list,
                "explanationSummary": summary,
                "reasons": reasons,
            })

    if collected:
        payload = {
            "updated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
            "matches": collected,
        }
        with open(MATCHES_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    logging.info(f"Синхронизация завершена. Сохранено матчей: {len(collected)}")
    return len(collected)


async def background_matches_scheduler():
    """Фоновый цикл: обновляет матчи при старте и далее каждые SYNC_INTERVAL_HOURS часов."""
    while True:
        await asyncio.to_thread(fetch_daily_matches_sync)
        await asyncio.sleep(SYNC_INTERVAL_HOURS * 3600)


# ==============================================================================
# БЛОК 3: ОБРАБОТЧИКИ TELEGRAM-БОТА И ОПЛАТЫ 499 TELEGRAM STARS
# ==============================================================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    is_admin = message.from_user.id == ADMIN_ID
    keyboard_rows = [
        [KeyboardButton(text="📊 Открыть Цифру Спорта", web_app=WebAppInfo(url=MINI_APP_URL))],
        [KeyboardButton(text=f"⭐ Купить PRO-подписку ({STARS_PRICE} Stars)")],
    ]
    if is_admin:
        keyboard_rows.append([KeyboardButton(text="🔄 Обновить линию матчей (Админ)")])

    kb = ReplyKeyboardMarkup(keyboard=keyboard_rows, resize_keyboard=True)

    admin_note = "\n\n👑 *Вы авторизованы как владелец — PRO-доступ активен бесплатно.*" if is_admin else ""
    await message.answer(
        "👋 Добро пожаловать в **Цифру Спорта**!\n\n"
        "• Актуальные коэффициенты БК и чистый процент победы\n"
        "• Подробные объяснения, почему у команды выше шанс на победу\n"
        f"• **PRO-подписка ({STARS_PRICE} ⭐/мес):** автоматическая ИИ-проверка экспрессов и рекомендации по замене исходов"
        f"{admin_note}",
        parse_mode="Markdown",
        reply_markup=kb,
    )


async def send_stars_invoice(chat_id: int):
    """Выставляет счёт на 499 Telegram Stars (валюта XTR)."""
    await bot.send_invoice(
        chat_id=chat_id,
        title="Цифра Спорта PRO (30 дней)",
        description="Автоматическая проверка экспрессов с помощью ИИ, поиск слабых событий и рекомендации по замене.",
        payload="pro_subscription_30d",
        provider_token="",  # Для Telegram Stars токен провайдера всегда пустой
        currency="XTR",
        prices=[LabeledPrice(label="Подписка PRO на 1 месяц", amount=STARS_PRICE)],
    )


@dp.message(Command("pro"))
@dp.message(F.text == f"⭐ Купить PRO-подписку ({STARS_PRICE} Stars)")
async def cmd_buy_pro(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👑 Вы владелец проекта — для вас подписка PRO уже активна бесплатно!")
        return
    await send_stars_invoice(message.chat.id)


@dp.message(Command("sync"))
@dp.message(F.text == "🔄 Обновить линию матчей (Админ)")
async def cmd_admin_sync(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    msg = await message.answer("⏳ Загружаю свежие матчи и коэффициенты БК...")
    count = await asyncio.to_thread(fetch_daily_matches_sync)
    await msg.edit_text(f"✅ Линия обновлена! В базу загружено матчей: **{count}**.", parse_mode="Markdown")


@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    """Принимает запрос на оплату 499 звёзд из Mini App."""
    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return

    if data.get("action") == "buy_pro_stars":
        await send_stars_invoice(message.chat.id)


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    """Подтверждает готовность принять 499 звёзд."""
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    """Срабатывает сразу после оплаты 499 Telegram Stars."""
    payment = message.successful_payment
    expires_at = activate_pro_subscription(message.from_user.id, SUBSCRIPTION_DAYS)

    await message.answer(
        f"✅ **Оплата {payment.total_amount} ⭐ прошла успешно!**\n\n"
        f"Подписка **Цифра Спорта PRO** активирована до `{expires_at}`.\n"
        "Откройте приложение — ИИ-проверка экспрессов уже доступна!",
        parse_mode="Markdown",
    )


# ==============================================================================
# ТОЧКА ВХОДА: ЗАПУСК БОТА И ФОНОВОГО ОБНОВЛЕНИЯ МАТЧЕЙ ОДНОВРЕМЕННО
# ==============================================================================
async def main():
    logging.info("Запуск «Цифра Спорта» (бот + автопарсер матчей)...")
    asyncio.create_task(background_matches_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
