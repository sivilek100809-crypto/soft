from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = 'beauty_start_secret_key_123'

try:
    with open('metro.txt', 'r', encoding='utf-8') as f:
        MOSCOW_METRO = [line.strip() for line in f if line.strip() and '.txt' not in line]
except FileNotFoundError:
    MOSCOW_METRO = ["Выхино", "Тверская", "МЦД Подольск"]

SALONS = [
    {"id": 1, "name": "Nail Studio", "metro": "Выхино", "desc": "Уютная студия у метро", "img": "https://images.unsplash.com/photo-1604654894610-df490651e56c?q=80&w=400"},
    {"id": 2, "name": "Beauty Bar", "metro": "Тверская", "desc": "Современный салон в центре", "img": "https://images.unsplash.com/photo-1632345031435-8797b2d58045?q=80&w=400"}
]

MASTERS = {
    1: {
        "id": 1, "name": "Алина Радченко", "role": "Мастер по сложному дизайну", "password": "alina123",
        "bio": "Опыт 6 месяцев. Рисую френч с закрытыми глазами! 🌸",
        "avatar": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?q=80&w=256&h=256&fit=crop",
        "price_list": [{"service": "Маникюр + Покрытие", "cost": "1500 ₽"}],
        "schedule": {"2026-06-01": {"10:00": True, "12:30": False}}
    },
    2: {
        "id": 2, "name": "Екатерина Смирнова", "role": "Мастер по наращиванию", "password": "katya777",
        "bio": "Опыт 3 года. Специализируюсь на скоростном однотоне.",
        "avatar": "https://images.unsplash.com/photo-1580489944761-15a19d654956?q=80&w=256&h=256&fit=crop",
        "price_list": [{"service": "Наращивание ногтей", "cost": "2900 ₽"}],
        "schedule": {"2026-06-01": {"09:00": True}}
    }
}

# 1. САМАЯ ПЕРВАЯ СТРАНИЦА: Выбор роли (Кто ты?)
@app.route('/')
def role_selection():
    return render_template('role.html')

# 2. СТРАНИЦА ДЛЯ КЛИЕНТОВ (Поиск салонов)
@app.route('/client')
def index():
    user_input = request.args.get('metro', '').strip()
    filtered_salons = [s for s in SALONS if user_input.lower() in s['metro'].lower()] if user_input else SALONS
    return render_template('index.html', salons=filtered_salons, selected_metro=user_input, metro_list=MOSCOW_METRO)

# 3. СТРАНИЦА ДЛЯ МАСТЕРОВ: Выбор своего профиля для входа
@app.route('/master/list')
def master_list():
    return render_template('master_list.html', masters=MASTERS.values())

@app.route('/master/<int:master_id>')
def master_profile(master_id):
    master = MASTERS.get(master_id, MASTERS[1])
    selected_date = request.args.get('date', list(master['schedule'].keys())[0] if master['schedule'] else '')
    slots = master['schedule'].get(selected_date, {})
    return render_template('master.html', master=master, selected_date=selected_date, slots=slots)

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
        date = request.form.get('date')
        
        if action == 'add_slot' and date:
            time = request.form.get('time')
            if time:
                if date not in master['schedule']: master['schedule'][date] = {}
                master['schedule'][date][time] = True
        elif action == 'toggle_slot':
            time = request.form.get('time')
            if date in master['schedule'] and time in master['schedule'][date]:
                master['schedule'][date][time] = not master['schedule'][date][time]
        elif action == 'delete_slot':
            time = request.form.get('time')
            if date in master['schedule'] and time in master['schedule'][date]:
                del master['schedule'][date][time]
                if not master['schedule'][date]: del master['schedule'][date]
        elif action == 'add_service':
            name = request.form.get('service_name')
            cost = request.form.get('service_cost')
            if name and cost: master['price_list'].append({"service": name, "cost": f"{cost} ₽"})
        elif action == 'delete_service':
            idx = int(request.form.get('service_index'))
            if 0 <= idx < len(master['price_list']): master['price_list'].pop(idx)
                    
        return redirect(url_for('master_dashboard', master_id=master_id, date=date))

    selected_date = request.args.get('date', list(master['schedule'].keys())[0] if master['schedule'] else '')
    slots = master['schedule'].get(selected_date, {})
    return render_template('dashboard.html', master=master, selected_date=selected_date, slots=slots)

if __name__ == '__main__':
    app.run(debug=True)