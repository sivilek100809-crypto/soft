import os
import sys
import json
import secrets
import threading
from datetime import date, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, abort

# --- Токен бота: из переменной окружения или из config.py (см. config.example.py) ---
try:
    from config import BOT_TOKEN as _CFG_TOKEN
except Exception:
    _CFG_TOKEN = ""
BOT_TOKEN = os.environ.get("BOT_TOKEN") or _CFG_TOKEN

# --- Библиотеки бота/планировщика подключаем мягко: без них сайт работает в режиме заглушки ---
try:
    import telebot
except ImportError:
    telebot = None
try:
    from apscheduler.schedulers.background import BackgroundScheduler
except ImportError:
    BackgroundScheduler = None

app = Flask(__name__)
app.secret_key = 'beauty_start_secret_key_123'

bot = telebot.TeleBot(BOT_TOKEN) if (telebot and BOT_TOKEN) else None
BOT_USERNAME = None  # заполнится при старте (нужно для ссылок t.me/...)

# ===========================================================
#  ДАННЫЕ
# ===========================================================
try:
    with open('metro.txt', 'r', encoding='utf-8') as f:
        MOSCOW_METRO = [line.strip() for line in f if line.strip() and '.txt' not in line]
except FileNotFoundError:
    MOSCOW_METRO = ["Выхино", "Тверская", "МЦД Подольск"]

SALONS = [
    {"id": 1, "name": "Nail Studio", "metro": "Выхино", "desc": "Уютная студия у метро", "img": "https://images.unsplash.com/photo-1604654894610-df490651e56c?q=80&w=400"},
    {"id": 2, "name": "Beauty Bar", "metro": "Тверская", "desc": "Современный салон в центре", "img": "https://images.unsplash.com/photo-1632345031435-8797b2d58045?q=80&w=400"}
]

# Пул названий и фото, чтобы сгенерировать салон для ЛЮБОЙ станции
SALON_NAMES = ["Nail Studio", "Beauty Bar", "Glam Room", "Velvet Nails",
               "Lash & Brow", "Lux Beauty", "Pink Atelier", "Lavender Spa"]
SALON_IMAGES = [
    "https://images.unsplash.com/photo-1604654894610-df490651e56c?q=80&w=400",
    "https://images.unsplash.com/photo-1632345031435-8797b2d58045?q=80&w=400",
    "https://images.unsplash.com/photo-1519014816548-bf5fe059798b?q=80&w=400",
    "https://images.unsplash.com/photo-1560066984-138dadb4c035?q=80&w=400",
]

def salons_for(station):
    """Возвращает салоны для станции: реальные, если есть, иначе генерирует пару."""
    real = [s for s in SALONS if s['metro'].lower() == station.lower()]
    if real:
        return real
    base = sum(ord(c) for c in station)  # стабильный «сид» по имени станции
    salons = []
    for i in range(2):
        salons.append({
            "id": (i % len(MASTERS)) + 1,  # ссылка на существующего мастера (1 или 2)
            "name": SALON_NAMES[(base + i) % len(SALON_NAMES)],
            "metro": station,
            "desc": f"Уютная студия рядом со станцией {station}",
            "img": SALON_IMAGES[(base + i) % len(SALON_IMAGES)],
        })
    return salons

MASTERS = {
    1: {
        "id": 1, "name": "Алина Радченко", "role": "Мастер по сложному дизайну", "password": "alina123",
        "bio": "Опыт 6 месяцев. Рисую френч с закрытыми глазами! 🌸",
        "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?q=80&w=256&h=256&fit=crop",
        "address": "Москва, ул. Хабаровская, 15 (м. Выхино)",
        "tg_chat_id": None,
        "price_list": [{"service": "Маникюр + Покрытие", "cost": "1500 ₽"}],
        "schedule": {"2026-06-01": {"10:00": True, "12:30": False}}
    },
    2: {
        "id": 2, "name": "Екатерина Смирнова", "role": "Мастер по наращиванию", "password": "katya777",
        "bio": "Опыт 3 года. Специализируюсь на скоростном однотоне.",
        "avatar": "https://images.unsplash.com/photo-1580489944761-15a19d654956?q=80&w=256&h=256&fit=crop",
        "address": "Москва, Тверская ул., 12 (м. Тверская)",
        "tg_chat_id": None,
        "price_list": [{"service": "Наращивание ногтей", "cost": "2900 ₽"}],
        "schedule": {"2026-06-01": {"09:00": True}}
    }
}

# Записи клиентов (хранятся в data.json)
BOOKINGS = []

# ===========================================================
#  СОХРАНЕНИЕ / ЗАГРУЗКА (data.json)
# ===========================================================
DATA_FILE = 'data.json'
_lock = threading.Lock()

def save_data():
    with _lock:
        data = {
            "masters": {str(mid): {"chat_id": m.get("tg_chat_id"), "address": m.get("address")}
                        for mid, m in MASTERS.items()},
            "bookings": BOOKINGS,
        }
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

def load_data():
    global BOOKINGS
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    for mid_str, info in data.get("masters", {}).items():
        mid = int(mid_str)
        if mid in MASTERS:
            if info.get("chat_id"):
                MASTERS[mid]["tg_chat_id"] = info["chat_id"]
            if info.get("address"):
                MASTERS[mid]["address"] = info["address"]
    BOOKINGS = data.get("bookings", [])

# ===========================================================
#  TELEGRAM: отправка и ссылки
# ===========================================================
def _console(s):
    """Безопасный вывод в консоль (на Windows cp1251 эмодзи не кодируются)."""
    try:
        print(s)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or 'utf-8'
        sys.stdout.write(s.encode(enc, 'replace').decode(enc) + "\n")

def send_tg(chat_id, text):
    """Отправляет сообщение в Telegram. Без токена — печатает в консоль."""
    if not bot or not chat_id:
        _console(f"[TG-заглушка] -> {chat_id}:\n{text}\n")
        return False
    try:
        bot.send_message(chat_id, text)
        return True
    except Exception as e:
        print("Ошибка отправки в Telegram:", e)
        return False

def tg_link(payload):
    """Ссылка вида https://t.me/<bot>?start=<payload> (или None, если бот не настроен)."""
    return f"https://t.me/{BOT_USERNAME}?start={payload}" if BOT_USERNAME else None

@app.context_processor
def inject_bot_helpers():
    return {"tg_link": tg_link, "bot_ready": bot is not None}

def notify_master_new_booking(booking):
    master = MASTERS.get(booking["master_id"])
    if not master:
        return
    full_name = f"{booking.get('client_surname', '')} {booking['client_name']}".strip()
    text = (
        "🔔 Новая запись!\n\n"
        f"👤 Клиент: {full_name}\n"
        f"📞 Телефон: {booking['client_phone']}\n"
        f"📅 Дата: {booking['date']}\n"
        f"⏰ Время: {booking['time']}"
    )
    send_tg(master.get("tg_chat_id"), text)

def send_reminders():
    """Напоминание клиенту за день до визита (запускается планировщиком)."""
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    changed = False
    for b in BOOKINGS:
        if b["date"] == tomorrow and b.get("client_chat_id") and not b.get("reminded"):
            master = MASTERS.get(b["master_id"], {})
            text = (
                "💅 Напоминание о записи!\n\n"
                f"Завтра, {b['date']} в {b['time']}\n"
                f"Мастер: {master.get('name', '')}\n"
                f"📍 Адрес: {master.get('address', 'уточните у мастера')}\n\n"
                "Ждём вас! ✨"
            )
            if send_tg(b["client_chat_id"], text):
                b["reminded"] = True
                changed = True
    if changed:
        save_data()

# ===========================================================
#  TELEGRAM: обработчики бота (/start с параметром)
# ===========================================================
if bot:
    @bot.message_handler(commands=['start'])
    def on_start(message):
        parts = message.text.split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""
        chat_id = message.chat.id

        if payload.startswith("master_"):
            try:
                mid = int(payload.split("_", 1)[1])
            except ValueError:
                mid = None
            if mid in MASTERS:
                MASTERS[mid]["tg_chat_id"] = chat_id
                save_data()
                bot.reply_to(message, f"✅ Готово, {MASTERS[mid]['name']}! Уведомления о новых записях подключены.")
            else:
                bot.reply_to(message, "Мастер не найден 🤔")
        elif payload:
            b = next((x for x in BOOKINGS if x["id"] == payload), None)
            if b:
                b["client_chat_id"] = chat_id
                save_data()
                bot.reply_to(message, f"✅ Принято! Напомню за день до визита — {b['date']} в {b['time']}.")
            else:
                bot.reply_to(message, "Запись не найдена 🤔")
        else:
            bot.reply_to(message, "Привет! Это бот записи YS.BEAUTY.LAB ✨\nПерейдите по ссылке с сайта, чтобы подключить уведомления.")

# ===========================================================
#  МАРШРУТЫ САЙТА
# ===========================================================
@app.route('/')
def role_selection():
    return render_template('role.html')

@app.route('/client/register', methods=['GET', 'POST'])
def client_register():
    if request.method == 'POST':
        surname = request.form.get('surname', '').strip()
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        if not (surname and name and phone):
            return render_template('register.html', error="Заполните все поля", client=None)
        session['client'] = {'surname': surname, 'name': name, 'phone': phone}
        return redirect(url_for('index'))
    return render_template('register.html', error=None, client=session.get('client'))

@app.route('/client')
def index():
    if 'client' not in session:
        return redirect(url_for('client_register'))
    user_input = request.args.get('metro', '').strip()
    if not user_input:
        filtered_salons = SALONS
    else:
        station = next((st for st in MOSCOW_METRO if st.lower() == user_input.lower()), None)
        if station:
            filtered_salons = salons_for(station)
        else:
            filtered_salons = [s for s in SALONS if user_input.lower() in s['metro'].lower()]
    return render_template('index.html', salons=filtered_salons, selected_metro=user_input, metro_list=MOSCOW_METRO)

@app.route('/master/list')
def master_list():
    return render_template('master_list.html', masters=MASTERS.values())

@app.route('/master/<int:master_id>')
def master_profile(master_id):
    master = MASTERS.get(master_id, MASTERS[1])
    selected_date = request.args.get('date', list(master['schedule'].keys())[0] if master['schedule'] else '')
    slots = master['schedule'].get(selected_date, {})
    return render_template('master.html', master=master, selected_date=selected_date, slots=slots)

# --- ЗАПИСЬ: данные клиента берём из регистрации (сессии), форма не нужна ---
@app.route('/book/<int:master_id>', methods=['POST'])
def book(master_id):
    master = MASTERS.get(master_id)
    if not master:
        abort(404)

    client = session.get('client')
    if not client:  # не зарегистрирован — отправляем на регистрацию
        return redirect(url_for('client_register'))

    date_str = request.form.get('date', '')
    time_str = request.form.get('time', '')

    # слот должен существовать и быть свободным
    if not master['schedule'].get(date_str, {}).get(time_str, False):
        return redirect(url_for('master_profile', master_id=master_id, date=date_str))

    token = secrets.token_urlsafe(8)
    booking = {
        "id": token, "master_id": master_id,
        "date": date_str, "time": time_str,
        "client_name": client['name'], "client_surname": client['surname'],
        "client_phone": client['phone'],
        "client_chat_id": None, "reminded": False,
    }
    master['schedule'][date_str][time_str] = False  # слот занят
    BOOKINGS.append(booking)
    save_data()
    notify_master_new_booking(booking)
    return redirect(url_for('booked', token=token))

# --- ЗАПИСЬ: подтверждение + ссылка на напоминания ---
@app.route('/booked/<token>')
def booked(token):
    booking = next((b for b in BOOKINGS if b["id"] == token), None)
    if not booking:
        abort(404)
    master = MASTERS.get(booking["master_id"], {})
    return render_template('booked.html', booking=booking, master=master)

# ===========================================================
#  КАБИНЕТ МАСТЕРА
# ===========================================================
@app.route('/master/<int:master_id>/login', methods=['GET', 'POST'])
def master_login(master_id):
    master = MASTERS.get(master_id, MASTERS[1])
    error = None
    if request.method == 'POST':
        input_password = request.form.get('password')
        if input_password == master['password']:
            session[f'master_auth_{master_id}'] = True
            return redirect(url_for('master_dashboard', master_id=master_id))
        else:
            error = "Неверный пароль! Доступ заблокирован ❌"
    return render_template('login.html', master=master, error=error)

@app.route('/master/<int:master_id>/logout')
def master_logout(master_id):
    session.pop(f'master_auth_{master_id}', None)
    return redirect(url_for('master_profile', master_id=master_id))

@app.route('/master/<int:master_id>/dashboard', methods=['GET', 'POST'])
def master_dashboard(master_id):
    if not session.get(f'master_auth_{master_id}'):
        return redirect(url_for('master_login', master_id=master_id))

    master = MASTERS.get(master_id, MASTERS[1])
    if request.method == 'POST':
        action = request.form.get('action')
        date_str = request.form.get('date')

        if action == 'add_slot' and date_str:
            time = request.form.get('time')
            if time:
                if date_str not in master['schedule']:
                    master['schedule'][date_str] = {}
                master['schedule'][date_str][time] = True
        elif action == 'toggle_slot':
            time = request.form.get('time')
            if date_str in master['schedule'] and time in master['schedule'][date_str]:
                master['schedule'][date_str][time] = not master['schedule'][date_str][time]
        elif action == 'delete_slot':
            time = request.form.get('time')
            if date_str in master['schedule'] and time in master['schedule'][date_str]:
                del master['schedule'][date_str][time]
                if not master['schedule'][date_str]:
                    del master['schedule'][date_str]
        elif action == 'add_service':
            name = request.form.get('service_name')
            cost = request.form.get('service_cost')
            if name and cost:
                master['price_list'].append({"service": name, "cost": f"{cost} ₽"})
        elif action == 'delete_service':
            idx = int(request.form.get('service_index'))
            if 0 <= idx < len(master['price_list']):
                master['price_list'].pop(idx)
        elif action == 'set_address':
            master['address'] = request.form.get('address', '').strip()
            save_data()

        return redirect(url_for('master_dashboard', master_id=master_id, date=date_str))

    selected_date = request.args.get('date', list(master['schedule'].keys())[0] if master['schedule'] else '')
    slots = master['schedule'].get(selected_date, {})
    return render_template('dashboard.html', master=master, selected_date=selected_date, slots=slots)

# ===========================================================
#  ЗАПУСК: Flask + бот (поллинг) + планировщик
# ===========================================================
load_data()  # подтянуть сохранённые адреса/чаты/записи

def _start_services():
    """Запуск бота (поллинг) и планировщика. Вызывается ОДИН раз — в рабочем процессе."""
    global BOT_USERNAME
    if bot:
        try:
            BOT_USERNAME = bot.get_me().username
            print(f"Бот @{BOT_USERNAME} запущен.", flush=True)
            threading.Thread(target=lambda: bot.infinity_polling(skip_pending=True), daemon=True).start()
        except Exception as e:
            print("Не удалось запустить бота (проверьте токен/интернет):", e, flush=True)
    else:
        print("Бот не настроен — режим заглушки (сообщения печатаются в консоль). См. config.example.py", flush=True)

    if BackgroundScheduler:
        scheduler = BackgroundScheduler(timezone="Europe/Moscow")
        scheduler.add_job(send_reminders, 'cron', hour=10, minute=0)  # каждый день в 10:00
        scheduler.start()
        send_reminders()  # разовая проверка при старте

if __name__ == '__main__':
    # Автоперезагрузка кода ВКЛЮЧЕНА: правки в .py применяются сами после сохранения.
    # Бот/планировщик стартуем только в рабочем процессе (WERKZEUG_RUN_MAIN),
    # чтобы при перезагрузке бот не запускался дважды (иначе конфликт 409 у Telegram).
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _start_services()
    app.run(debug=True)
