import os
import json
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

load_dotenv()

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Настройки прокси (заполните вашими данными)
PROXY_IP = os.getenv("PROXY_IP")
PROXY_PORT = os.getenv("PROXY_PORT")
PROXY_USER = os.getenv("PROXY_USER")
PROXY_PASS = os.getenv("PROXY_PASS")

proxy = f"http://{PROXY_USER}:{PROXY_PASS}@{PROXY_IP}:{PROXY_PORT}"

# Укажите здесь ID двух пользователей через запятую (без кавычек)
ALLOWED_USERS = (os.getenv("USER_ID_1"), os.getenv("USER_ID_2"))

TARGET_URL = os.getenv("TARGET_URL")
JSON_FILE = "boxing_odds.json"
CHECK_INTERVAL = 1500


# --- АСИНХРОННЫЙ ПАРСЕР ---
async def get_entire_html_async(url):
    """Асинхронное получение HTML-кода страницы через Playwright."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--start-maximized']
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU"
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            print(f"[Парсер] Подключаемся к {url}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Асинхронное ожидание динамических элементов
            await asyncio.sleep(10)

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2);")
            await asyncio.sleep(2)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            await asyncio.sleep(2)

            full_html_code = await page.content()
            await browser.close()
            return full_html_code
        except Exception as e:
            print(f"[Парсер] Ошибка при получении HTML: {e}")
            await browser.close()
            return None


def parse_html_to_data(html_content):
    """Преобразование HTML в структурированный список боев."""
    soup = BeautifulSoup(html_content, 'lxml')
    events = soup.find_all("div", class_="sport-base-event--W4qkO")

    all_fights_data = []
    for i in events:
        try:
            fight_name = i.find("a").text.strip()
            c = i.find_all("span", class_="value--OUKql value--u6eyY")
            coefficients = [t.text.strip() for t in c[:3]]

            fight_dict = {
                "fight": fight_name,
                "coefficients": {
                    "W1": coefficients[0] if len(coefficients) > 0 else "-",
                    "X": coefficients[1] if len(coefficients) > 1 else "-",
                    "W2": coefficients[2] if len(coefficients) > 2 else "-"
                }
            }
            all_fights_data.append(fight_dict)
        except Exception as e:
            print(f"[Парсер] Ошибка при обработке матча: {e}")
            continue
    return all_fights_data


# --- ЛОГИКА МОНИТОРИНГА И СРАВНЕНИЯ ---
def load_old_data():
    """Загрузка старых данных из JSON-файла."""
    if os.path.exists(JSON_FILE):
        try:
            with open(JSON_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_new_data(data):
    """Сохранение новых данных в JSON-файл."""
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


async def check_for_updates(bot: Bot):
    """Фоновая задача, которая проверяет сайт каждые 25 минут."""
    while True:
        print("\n[Мониторинг] Запуск плановой проверки коэффициентов...")
        html = await get_entire_html_async(TARGET_URL)

        if not html:
            print("[Мониторинг] Не удалось получить данные с сайта. Пропускаем.")
            await asyncio.sleep(CHECK_INTERVAL)
            continue

        current_fights = parse_html_to_data(html)
        old_fights = load_old_data()

        # Создаем словарь старых боев для быстрого поиска по названию
        old_fights_dict = {f["fight"]: f["coefficients"] for f in old_fights}

        # Списки для уведомлений
        notifications = []

        for current in current_fights:
            name = current["fight"]
            cur_coefs = current["coefficients"]

            # 1. Проверяем на наличие нового боя
            if name not in old_fights_dict:
                msg = (f"🆕 <b>Новый бой добавлен!</b>\n"
                       f"🥊 {name}\n"
                       f"📊 Коэффициенты: П1: {cur_coefs['W1']} | Х: {cur_coefs['X']} | П2: {cur_coefs['W2']}")
                notifications.append(msg)
            else:
                # 2. Проверяем изменения коэффициентов в существующем бою
                old_coefs = old_fights_dict[name]
                if cur_coefs != old_coefs:
                    msg = (f"📈 <b>Изменение коэффициентов!</b>\n"
                           f"🥊 {name}\n"
                           f"Было:  П1: {old_coefs['W1']} | Х: {old_coefs['X']} | П2: {old_coefs['W2']}\n"
                           f"Стало: П1: {cur_coefs['W1']} | Х: {cur_coefs['X']} | П2: {cur_coefs['W2']}")
                    notifications.append(msg)

        # Отправляем уведомления, если они есть
        if notifications:
            print(f"[Мониторинг] Найдено изменений: {len(notifications)}. Отправка уведомлений...")
            for text in notifications:
                # Отправка каждому пользователю из белого списка
                for user_id in ALLOWED_USERS:
                    try:
                        await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
                        await asyncio.sleep(0.1)  # Небольшая пауза между отправками
                    except Exception as e:
                        print(f"[Ошибка отправки пользователю {user_id}]: {e}")
                await asyncio.sleep(0.4)  # Защита от спам-лимитов Telegram после отправки сообщения всем
        else:
            print("[Мониторинг] Изменений не обнаружено.")

        # Перезаписываем базу актуальными данными
        save_new_data(current_fights)

        print(f"[Мониторинг] Проверка завершена. Спим {CHECK_INTERVAL} секунд.")
        await asyncio.sleep(CHECK_INTERVAL)


# --- ТЕЛЕГРАМ БОТ ХЕНДЛЕРЫ ---
session = AiohttpSession(proxy=proxy)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Обработчик команды /start."""
    # Проверка на наличие пользователя в белом списке
    if message.from_user.id not in ALLOWED_USERS:
        await message.answer("У вас нет доступа.")
        return

    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        f"У вас есть доступ к мониторингу.\n"
        f"Бот автоматически проверяет сайт каждые 5 минут и пришлет уведомление обоим пользователям при изменениях."
    )


async def main():
    # Запускаем фоновую задачу проверки сайта параллельно с ботом
    asyncio.create_task(check_for_updates(bot))

    print("[Система] Бот успешно запущен. Ожидание сообщений...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[Система] Бот остановлен.")
