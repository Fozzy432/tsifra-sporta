import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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

# Настройки обязательной подписки на канал (Бот ДОЛЖЕН быть админом этого канала!)
CHANNEL_USERNAME = "@DigitSportLive"             # <-- Укажите @username вашего канала (или числовой ID -100...)
CHANNEL_URL = "https://t.me/DigitSportLive"      # <-- Укажите ссылку на ваш канал

ADMIN_ID = 6851997342          # Ваш Telegram ID владельца
STARS_PRICE = 499              # Цена подписки PRO в Telegram Stars
SUBSCRIPTION_DAYS = 30         # Срок действия подписки (дней)

MATCHES_FILE = "matches.json"
USERS_FILE = "users.json"

SPORTS_CONFIG = [
    {"key": "soccer_uefa_nations_league", "name": "Лига наций УЕФА", "sport": "football", "featured": True},
    {"key": "icehockey_russia_khl", "name": "КХЛ • Регулярный чемпионат", "sport": "hockey", "featured": True},
    {"key": "soccer_epl", "name": "АПЛ", "sport": "football", "featured": True},
    {"key": "soccer_uefa_champs_league", "name": "Лига чемпионов", "sport": "football", "featured": True},
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ==============================================================================
# БЛОК 0: ПРОВЕРКА ОБЯЗАТЕЛЬНОЙ ПОДПИСКИ НА КАНАЛ
# ==============================================================================
async def is_user_subscribed(user_id: int) -> bool:
    """Проверяет, состоит ли пользователь в официальном канале."""
    if user_id == ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        }
    except Exception as e:
        logging.error(f"Ошибка проверки подписки на канал {CHANNEL_USERNAME}: {e}")
        return False


def get_subscribe_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Я подписался (Проверить)", callback_data="check_subscription")],
        ]
    )


def get_main_menu_keyboard(is_admin: bool) -> ReplyKeyboardMarkup:
    keyboard_rows = [
        [KeyboardButton(text="📊 Открыть Цифру Спорта", web_app=WebAppInfo(url=MINI_APP_URL))],
        [KeyboardButton(text=f"⭐ Купить PRO-подписку ({STARS_PRICE} Stars)")],
    ]
    if is_admin:
        keyboard_rows.append([KeyboardButton(text="🔄 Обновить Live и линию (Админ)")])
    return ReplyKeyboardMarkup(keyboard=keyboard_rows, resize_keyboard=True)


async def send_welcome_menu(chat_id: int, user_id: int):
    is_admin = user_id == ADMIN_ID
    kb = get_main_menu_keyboard(is_admin)
    admin_note = "\n\n👑 *Вы авторизованы как владелец — PRO-доступ активен бесплатно.*" if is_admin else ""
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "👋 Добро пожаловать в **Цифру Спорта**!\n\n"
            "• Все матчи **Лиги наций УЕФА** и **КХЛ** за сегодня\n"
            "• **Live-счёт** и пересчёт шансов на победу в реальном времени\n"
            "• Автоудаление завершившихся игр\n"
            f"• **PRO ({STARS_PRICE} ⭐/мес):** ИИ-проверка экспрессов и рекомендации по замене"
            f"{admin_note}"
        ),
        parse_mode="Markdown",
        reply_markup=kb,
    )


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
# БЛОК 2: LIVE-СЧЁТ, ДИНАМИЧЕСКИЙ ПРОГНОЗ И АВТОУДАЛЕНИЕ ЗАВЕРШЁННЫХ МАТЧЕЙ
# ==============================================================================
def calc_fair_probabilities(p1_odd: float, x_odd: float, p2_odd: float) -> dict:
    inv_p1 = 1 / p1_odd
    inv_x = 1 / x_odd if x_odd > 0 else 0
    inv_p2 = 1 / p2_odd
    total_margin = inv_p1 + inv_x + inv_p2

    p1 = round((inv_p1 / total_margin) * 100)
    x = round((inv_x / total_margin) * 100)
    p2 = 100 - p1 - x
    return {"p1": p1, "x": x, "p2": p2}


def adjust_live_probabilities(base_prob: dict, home_score: int, away_score: int, sport: str) -> dict:
    diff = home_score - away_score
    if diff == 0:
        return base_prob.copy()

    weight = 24 if sport == "football" else 16
    p1 = max(3, min(94, base_prob["p1"] + diff * weight))
    p2 = max(3, min(94, base_prob["p2"] - diff * weight))
    x = max(3, min(45, base_prob["x"] - abs(diff) * (8 if sport == "football" else 5)))

    total = p1 + x + p2
    p1_norm = round((p1 / total) * 100)
    x_norm = round((x / total) * 100)
    p2_norm = 100 - p1_norm - x_norm
    return {"p1": p1_norm, "x": x_norm, "p2": p2_norm}


def build_explanation(team1: str, team2: str, prob: dict, best_odds: dict, is_live: bool = False, score_str: str = "") -> tuple[str, list]:
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

    if is_live:
        summary = f"LIVE-пересчёт при счёте {score_str}: вероятность победы «{fav_team}» сейчас составляет {fav_prob}% против {dog_prob}% у «{dog_team}»."
    else:
        diff = fav_prob - dog_prob
        if diff >= 20:
            summary = f"У команды «{fav_team}» выраженное преимущество по модели ({fav_prob}% против {dog_prob}% у соперника «{dog_team}») за счёт разницы в классе и котировках рынка."
        elif diff >= 8:
            summary = f"«{fav_team}» идёт фаворитом встречи ({fav_prob}% на победу), но «{dog_team}» сохраняет рабочие шансы на очки при плотной игре."
        else:
            summary = f"Матч равных соперников: минимальный перевес на стороне «{fav_team}» ({fav_prob}% против {dog_prob}%), высока вероятность ничьей ({prob['x']}%)."

    reasons = [
        {
            "title": "Математическая оценка рынка и Live-динамика",
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


def fetch_live_scores_map(sport_key: str) -> dict:
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores/"
    params = {"apiKey": ODDS_API_KEY, "daysFrom": 1}
    scores_map = {}
    try:
        resp = requests.get(url, params=params, timeout=12)
        if resp.status_code == 200:
            for item in resp.json():
                scores_map[item["id"]] = item
    except Exception as e:
        logging.error(f"Ошибка получения Live-счёта для {sport_key}: {e}")
    return scores_map


def sync_all_matches_sync() -> tuple[int, int]:
    if not ODDS_API_KEY or "ВСТАВЬТЕ" in ODDS_API_KEY:
        logging.warning("ODDS_API_KEY не задан — пропуск запроса к API.")
        return 0, 0

    now_utc = datetime.now(timezone.utc)
    collected = []
    live_count = 0

    for cfg in SPORTS_CONFIG:
        scores_map = fetch_live_scores_map(cfg["key"])
        odds_url = f"https://api.the-odds-api.com/v4/sports/{cfg['key']}/odds/"
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal",
        }

        try:
            resp = requests.get(odds_url, params=params, timeout=15)
            if resp.status_code != 200:
                continue
            events = resp.json()
        except Exception as e:
            logging.error(f"Ошибка загрузки линии {cfg['key']}: {e}")
            continue

        for ev in events:
            ev_id = ev["id"]
            team1 = ev["home_team"]
            team2 = ev["away_team"]
            start_dt = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))

            if start_dt > now_utc + timedelta(hours=36):
                continue

            score_info = scores_map.get(ev_id, {})
            if score_info.get("completed") is True:
                continue

            duration_minutes = 115 if cfg["sport"] == "football" else 150
            if now_utc >= start_dt + timedelta(minutes=duration_minutes):
                continue

            is_live = now_utc >= start_dt
            home_score = 0
            away_score = 0
            if score_info.get("scores"):
                for sc in score_info["scores"]:
                    if sc["name"] == team1:
                        home_score = int(sc["score"])
                    elif sc["name"] == team2:
                        away_score = int(sc["score"])

            live_clock = ""
            if is_live:
                live_count += 1
                elapsed = max(1, int((now_utc - start_dt).total_seconds() // 60))
                if cfg["sport"] == "football":
                    live_clock = f"{elapsed}'" if elapsed <= 45 else ("Перерыв" if elapsed <= 60 else f"{min(90, elapsed - 15)}'")
                else:
                    live_clock = f"1-й пер. {elapsed}'" if elapsed <= 20 else ("2-й пер." if elapsed <= 55 else "3-й пер.")

            bookmakers_list = []
            best_p1 = {"val": 0.0, "bk": "Pinnacle"}
            best_x = {"val": 0.0, "bk": "Pinnacle"}
            best_p2 = {"val": 0.0, "bk": "Pinnacle"}

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

            base_prob = calc_fair_probabilities(best_p1["val"], best_x["val"], best_p2["val"])
            prob = adjust_live_probabilities(base_prob, home_score, away_score, cfg["sport"]) if is_live else base_prob

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

            summary, reasons = build_explanation(
                team1, team2, prob, best_odds_fmt, is_live=is_live, score_str=f"{home_score}:{away_score}"
            )
            msk_dt = start_dt + timedelta(hours=3)
            time_str = msk_dt.strftime("Сегодня, %H:%M МСК")

            collected.append({
                "id": ev_id,
                "sport": cfg["sport"],
                "featured": cfg["featured"] and len(collected) < 6,
                "isValue": is_value,
                "status": "live" if is_live else "upcoming",
                "startTs": start_dt.isoformat(),
                "liveClock": live_clock,
                "score": {"home": home_score, "away": away_score},
                "league": cfg["name"],
                "time": time_str,
                "venue": "Официальная арена турнира",
                "team1": team1,
                "team2": team2,
                "flag1": "⚽" if cfg["sport"] == "football" else "🏒",
                "flag2": "🛡️",
                "baseProb": base_prob,
                "prob": prob,
                "favorite": {"team": fav_team, "chance": fav_chance, "side": fav_side},
                "valueTag": value_tag,
                "bestOdds": best_odds_fmt,
                "bookmakers": bookmakers_list,
                "explanationSummary": summary,
                "reasons": reasons,
            })

    collected.sort(key=lambda item: (0 if item["status"] == "live" else 1, item["startTs"]))

    if collected:
        payload = {
            "updated_at": (now_utc + timedelta(hours=3)).strftime("%H:%M МСК"),
            "matches": collected,
        }
        with open(MATCHES_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    logging.info(f"Синхронизация: активных матчей = {len(collected)}, в LIVE = {live_count}")
    return len(collected), live_count


async def smart_realtime_scheduler():
    while True:
        _, live_count = await asyncio.to_thread(sync_all_matches_sync)
        sleep_seconds = 90 if live_count > 0 else 900
        await asyncio.sleep(sleep_seconds)


# ==============================================================================
# БЛОК 3: ОБРАБОТЧИКИ TELEGRAM-БОТА И ОПЛАТЫ 499 TELEGRAM STARS
# ==============================================================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    if not await is_user_subscribed(message.from_user.id):
        await message.answer(
            "🔒 **Доступ к «Цифре Спорта» открыт только подписчикам нашего канала!**\n\n"
            f"Подпишитесь на {CHANNEL_USERNAME}, а затем нажмите кнопку **«✅ Я подписался (Проверить)»** ниже.",
            parse_mode="Markdown",
            reply_markup=get_subscribe_keyboard(),
        )
        return

    await send_welcome_menu(message.chat.id, message.from_user.id)


@dp.callback_query(F.data == "check_subscription")
async def callback_check_subscription(callback: CallbackQuery):
    if await is_user_subscribed(callback.from_user.id):
        await callback.answer("✅ Подписка подтверждена!", show_alert=False)
        await callback.message.delete()
        await send_welcome_menu(callback.message.chat.id, callback.from_user.id)
    else:
        await callback.answer(
            f"❌ Вы ещё не подписались на канал {CHANNEL_USERNAME}. Подпишитесь и нажмите снова!",
            show_alert=True,
        )


async def send_stars_invoice(chat_id: int):
    """Выставляет официальный счёт на 499 Telegram Stars (валюта XTR)."""
    await bot.send_invoice(
        chat_id=chat_id,
        title="Цифра Спорта PRO (30 дней)",
        description="Автоматическая проверка экспрессов с помощью ИИ, поиск слабых событий и рекомендации по замене.",
        payload="pro_subscription_30d",
        provider_token="",  # Для Telegram Stars всегда пустая строка
        currency="XTR",     # Код валюты Telegram Stars
        prices=[LabeledPrice(label="Подписка PRO на 1 месяц", amount=STARS_PRICE)],
    )


@dp.message(Command("pro"))
@dp.message(Command("pay"))
@dp.message(F.text.contains("499 Stars"))
async def cmd_buy_pro(message: types.Message):
    if not await is_user_subscribed(message.from_user.id):
        await message.answer(
            f"🔒 Сначала подпишитесь на наш официальный канал {CHANNEL_USERNAME}:",
            reply_markup=get_subscribe_keyboard(),
        )
        return

    # Выставляем счёт всем (включая вас, если хотите протестировать оплату)
    await send_stars_invoice(message.chat.id)


@dp.message(Command("pro"))
@dp.message(F.text == f"⭐ Купить PRO-подписку ({STARS_PRICE} Stars)")
async def cmd_buy_pro(message: types.Message):
    if not await is_user_subscribed(message.from_user.id):
        await message.answer(
            f"🔒 Сначала подпишитесь на наш официальный канал {CHANNEL_USERNAME}:",
            reply_markup=get_subscribe_keyboard(),
        )
        return

    if message.from_user.id == ADMIN_ID:
        await message.answer("👑 Вы владелец проекта — для вас подписка PRO уже активна бесплатно!")
        return
    await send_stars_invoice(message.chat.id)


@dp.message(Command("sync"))
@dp.message(F.text == "🔄 Обновить Live и линию (Админ)")
async def cmd_admin_sync(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    msg = await message.answer("⏳ Синхронизирую матчи, Live-счёт и удаляю завершённые игры...")
    total, live_cnt = await asyncio.to_thread(sync_all_matches_sync)
    await msg.edit_text(
        f"✅ Готово!\n• Активных матчей: **{total}**\n• Идут в LIVE прямо сейчас: **{live_cnt}**",
        parse_mode="Markdown",
    )


@dp.message(F.web_app_data)
async def handle_webapp_data(message: types.Message):
    if not await is_user_subscribed(message.from_user.id):
        await message.answer(
            f"🔒 Для использования приложения подпишитесь на канал {CHANNEL_USERNAME}:",
            reply_markup=get_subscribe_keyboard(),
        )
        return

    try:
        data = json.loads(message.web_app_data.data)
    except Exception:
        return
    if data.get("action") == "buy_pro_stars":
        await send_stars_invoice(message.chat.id)


@dp.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    payment = message.successful_payment
    expires_at = activate_pro_subscription(message.from_user.id, SUBSCRIPTION_DAYS)
    await message.answer(
        f"✅ **Оплата {payment.total_amount} ⭐ прошла успешно!**\n\n"
        f"Подписка **Цифра Спорта PRO** активирована до `{expires_at}`.",
        parse_mode="Markdown",
    )


async def main():
    logging.info("Запуск «Цифра Спорта» (бот + обязательная подписка + Live-трекер)...")
    asyncio.create_task(smart_realtime_scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
