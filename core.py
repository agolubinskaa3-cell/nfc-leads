# -*- coding: utf-8 -*-
"""Ядро: работа с API 2ГИС, база SQLite, экспорт в Excel."""
import os
import re
import sqlite3
import threading
import time
from datetime import datetime
from urllib.parse import quote

import requests

API_URL = "https://catalog.api.2gis.com/3.0/items"
PAGE_SIZE = 50
MAX_PAGES = 10
FIELDS_FULL = "items.point,items.address,items.contact_groups,items.reviews"
FIELDS_BASIC = "items.point,items.address,items.contact_groups"

STATUSES = ["Новый", "Позвонил", "Думает", "Купил", "Отказался"]

CATEGORIES = {
    "Кафе и рестораны": ["ресторан", "кафе"],
    "Кофейни": ["кофейня"],
    "Барбершопы": ["барбершоп"],
    "Салоны красоты": ["салон красоты"],
    "Парикмахерские": ["парикмахерская"],
    "Магазины": ["магазин"],
    "Автомойки": ["автомойка"],
    "Автосервисы": ["автосервис"],
    "Ногтевые студии": ["ногтевая студия"],
    "Фитнес-клубы": ["фитнес клуб"],
    "Стоматологии": ["стоматология"],
}

DISTRICTS = [
    "Весь Краснодар", "Центр", "Фестивальный", "Юбилейный", "Гидростроителей",
    "Комсомольский", "Черёмушки", "Пашковский", "Западный", "Музыкальный",
    "Славянский", "Московский", "Энка", "Репино", "Автограф", "Немецкая деревня",
    "Покровка", "Молодёжный", "Дубинка", "Super Dubinka", "Табачная фабрика",
]
DISTRICTS = [d for d in DISTRICTS if d != "Super Dubinka"]

RADIUS = {"500 м": 500, "1 км": 1000, "2 км": 2000, "3 км": 3000, "5 км": 5000}


class ApiError(Exception):
    pass


def _norm(s):
    return re.sub(r"[^0-9a-zа-яё]", "", (s or "").lower())


class Database:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS leads(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gis_id TEXT UNIQUE, dedupe TEXT UNIQUE,
                name TEXT, address TEXT, phone TEXT,
                yandex_url TEXT, gis_url TEXT, site TEXT,
                rating REAL, reviews INTEGER,
                category TEXT, district TEXT,
                status TEXT DEFAULT 'Новый', called INTEGER DEFAULT 0,
                comment TEXT DEFAULT '', created TEXT);
            CREATE TABLE IF NOT EXISTS progress(
                task TEXT PRIMARY KEY, page INTEGER, done INTEGER);
            CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
            """)
            self.conn.commit()

    # --- настройки ---
    def get_setting(self, k, default=""):
        with self.lock:
            r = self.conn.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_setting(self, k, v):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, str(v)))
            self.conn.commit()

    # --- прогресс (пауза/продолжение) ---
    def get_progress(self, task):
        with self.lock:
            r = self.conn.execute("SELECT page, done FROM progress WHERE task=?", (task,)).fetchone()
        return (r["page"], r["done"]) if r else (1, 0)

    def set_progress(self, task, page, done):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO progress VALUES(?,?,?)", (task, page, done))
            self.conn.commit()

    def reset_progress(self, task):
        self.set_progress(task, 1, 0)

    # --- заведения ---
    def add_lead(self, d):
        """True — добавлено новое, False — дубликат (данные рейтинга обновляются)."""
        dedupe = _norm(d["name"]) + "|" + _norm(d["address"])
        with self.lock:
            try:
                self.conn.execute(
                    """INSERT INTO leads(gis_id,dedupe,name,address,phone,yandex_url,gis_url,
                       site,rating,reviews,category,district,created)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (d["gis_id"], dedupe, d["name"], d["address"], d["phone"], d["yandex_url"],
                     d["gis_url"], d["site"], d["rating"], d["reviews"], d["category"],
                     d["district"], datetime.now().strftime("%Y-%m-%d %H:%M")))
                self.conn.commit()
                return True
            except sqlite3.IntegrityError:
                self.conn.execute(
                    "UPDATE leads SET rating=?, reviews=? WHERE (gis_id=? OR dedupe=?) AND ? IS NOT NULL",
                    (d["rating"], d["reviews"], d["gis_id"], dedupe, d["rating"]))
                self.conn.commit()
                return False

    def query(self, status="Все", mode="Все", text="", few=20, low=4.0, limit=200):
        sql, args = "SELECT * FROM leads WHERE 1=1", []
        if status and status != "Все":
            sql += " AND status=?"; args.append(status)
        if mode == "Мало отзывов":
            sql += " AND reviews IS NOT NULL AND reviews<=?"; args.append(few)
        elif mode == "Низкий рейтинг":
            sql += " AND rating IS NOT NULL AND rating<?"; args.append(low)
        elif mode == "Не звонили":
            sql += " AND called=0"
        if text:
            sql += " AND (LOWER(name) LIKE ? OR LOWER(address) LIKE ?)"
            t = "%" + text.lower() + "%"; args += [t, t]
        sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]

    def update_lead(self, lead_id, status, called, comment):
        with self.lock:
            self.conn.execute("UPDATE leads SET status=?, called=?, comment=? WHERE id=?",
                              (status, 1 if called else 0, comment, lead_id))
            self.conn.commit()

    def all_leads(self):
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM leads ORDER BY id").fetchall()]


def parse_item(item, category, district):
    gid = str(item.get("id", "")).split("_")[0]
    name = (item.get("name") or "").strip()
    address = (item.get("address_name") or "").strip()
    phones, site = [], ""
    for g in item.get("contact_groups") or []:
        for c in g.get("contacts") or []:
            t = c.get("type")
            if t == "phone":
                v = c.get("text") or c.get("value")
                if v and v not in phones:
                    phones.append(v)
            elif t == "website" and not site:
                v = c.get("text") or c.get("value") or ""
                if v and not v.startswith("http"):
                    v = "http://" + v
                site = v
    rev = item.get("reviews") or {}
    rating = rev.get("general_rating", rev.get("org_rating"))
    count = rev.get("general_review_count", rev.get("org_review_count"))
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    try:
        count = int(count) if count is not None else None
    except (TypeError, ValueError):
        count = None
    return {
        "gis_id": gid or _norm(name + address), "name": name, "address": address,
        "phone": "; ".join(phones), "site": site, "rating": rating, "reviews": count,
        "gis_url": "https://2gis.ru/krasnodar/firm/%s" % gid if gid else "",
        # Яндекс Карты: ссылка-поиск по названию и адресу (официальный API организаций платный)
        "yandex_url": "https://yandex.ru/maps/35/krasnodar/search/" + quote("%s %s" % (name, address)),
        "category": category, "district": district,
    }


class Searcher:
    def __init__(self, db, key, log):
        self.db, self.key, self.log = db, key, log
        self.fields = FIELDS_FULL

    def _get(self, params):
        params = dict(params, key=self.key, locale="ru_RU")
        last = ""
        for attempt in range(3):
            try:
                r = requests.get(API_URL, params=params, timeout=20)
            except requests.RequestException as e:
                last = "нет связи с сервером 2ГИС (%s)" % type(e).__name__
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = "сервер 2ГИС занят (код %s)" % r.status_code
                time.sleep(3 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                raise ApiError("Некорректный ответ сервера 2ГИС (код %s)" % r.status_code)
            code = (data.get("meta") or {}).get("code", r.status_code)
            if code == 200:
                return data
            msg = ((data.get("meta") or {}).get("error") or {}).get("message", "")
            if code == 404:
                return {"result": {"items": [], "total": 0}}
            if code in (401, 403):
                raise ApiError("Ключ 2ГИС не принят или исчерпан лимит (%s). Проверьте ключ." % msg)
            if code == 400 and "field" in msg.lower() and self.fields == FIELDS_FULL:
                self.log("Ваш ключ не отдаёт рейтинг/отзывы — ищу без них.")
                self.fields = FIELDS_BASIC
                params["fields"] = self.fields
                continue
            raise ApiError("Ошибка 2ГИС %s: %s" % (code, msg))
        raise ApiError("Не удалось получить данные: " + last)

    def geocode(self, address):
        data = self._get({"q": "Краснодар " + address, "fields": "items.point", "page_size": 1})
        items = (data.get("result") or {}).get("items") or []
        for it in items:
            p = it.get("point")
            if p:
                return "%s,%s" % (p["lon"], p["lat"])
        raise ApiError("Адрес не найден: " + address)

    def run(self, queries, district, address, radius, stop, fresh=False):
        """queries: [(название категории, [поисковые фразы])]. Возвращает число новых."""
        point = None
        if address:
            self.log("Определяю адрес: %s" % address)
            point = self.geocode(address)
        place = district if district and district != "Весь Краснодар" else ""
        total_new = 0
        for label, phrases in queries:
            for phrase in phrases:
                if stop.is_set():
                    return total_new
                task = "|".join([phrase, place, address or "", str(radius)])
                if fresh:
                    self.db.reset_progress(task)
                page, done = self.db.get_progress(task)
                if done:
                    self.log("Уже собрано: «%s». Для обновления — «Поиск новых»." % phrase)
                    continue
                self.log("Ищу: %s%s" % (phrase, (", " + place) if place else ""))
                while page <= MAX_PAGES:
                    if stop.is_set():
                        self.log("Пауза. Нажмите «Старт», чтобы продолжить.")
                        return total_new
                    params = {"q": phrase if point else ("%s %s Краснодар" % (phrase, place)).replace("  ", " "),
                              "type": "branch", "page": page, "page_size": PAGE_SIZE,
                              "fields": self.fields}
                    if point:
                        params.update(point=point, radius=radius, sort="distance")
                    data = self._get(params)
                    res = data.get("result") or {}
                    items = res.get("items") or []
                    new = 0
                    for it in items:
                        d = parse_item(it, label, place or ("рядом с " + address if address else "Весь город"))
                        if d["name"] and self.db.add_lead(d):
                            new += 1
                    total_new += new
                    self.log("  страница %d: найдено %d, новых %d" % (page, len(items), new))
                    page += 1
                    finished = len(items) < PAGE_SIZE or (page - 1) * PAGE_SIZE >= res.get("total", 0)
                    self.db.set_progress(task, page, 1 if finished else 0)
                    if finished:
                        break
                    time.sleep(0.3)
                else:
                    self.db.set_progress(task, page, 1)
        return total_new


def export_xlsx(path, leads):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = "Клиенты"
    head = ["Название", "Адрес", "Телефон", "Яндекс Карты", "2ГИС", "Сайт", "Отзывов",
            "Рейтинг", "Категория", "Район", "Статус", "Звонили", "Комментарий", "Добавлено"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2F6FDE")
        c.alignment = Alignment(horizontal="center", vertical="center")
    colors = {"Купил": "C6EFCE", "Отказался": "F4CCCC", "Думает": "FFF2CC", "Позвонил": "DDEBF7"}
    for l in leads:
        ws.append([l["name"], l["address"], l["phone"], l["yandex_url"], l["gis_url"], l["site"],
                   l["reviews"], l["rating"], l["category"], l["district"], l["status"],
                   "Да" if l["called"] else "Нет", l["comment"], l["created"]])
        col = colors.get(l["status"])
        if col:
            ws.cell(row=ws.max_row, column=11).fill = PatternFill("solid", fgColor=col)
    for i, w in enumerate([32, 40, 22, 30, 30, 28, 10, 10, 20, 18, 12, 10, 36, 17], 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    return path
