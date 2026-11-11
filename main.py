# -*- coding: utf-8 -*-
"""NFC Клиенты — поиск потенциальных клиентов (Android, Kivy)."""
import os
import re
import threading
import webbrowser
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.utils import platform

import core

BG = (0.06, 0.07, 0.10, 1)
CARD = (0.13, 0.15, 0.20, 1)
ACCENT = (0.18, 0.44, 0.87, 1)
GREEN = (0.15, 0.6, 0.35, 1)
RED = (0.75, 0.25, 0.25, 1)
GREY = (0.3, 0.33, 0.4, 1)
WHITE = (1, 1, 1, 1)
STATUS_COLOR = {"Новый": CARD, "Позвонил": (0.12, 0.25, 0.4, 1), "Думает": (0.35, 0.3, 0.1, 1),
                "Купил": (0.1, 0.35, 0.2, 1), "Отказался": (0.35, 0.15, 0.15, 1)}


def lbl(text, size=15, bold=False, color=WHITE):
    l = Label(text=text, font_size=sp(size), bold=bold, color=color, size_hint_y=None,
              halign="left", valign="middle", markup=True)
    l.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
    l.bind(texture_size=lambda i, s: setattr(i, "height", s[1] + dp(6)))
    return l


def btn(text, cb=None, color=ACCENT, h=46):
    b = Button(text=text, background_normal="", background_color=color, size_hint_y=None,
               height=dp(h), font_size=sp(15))
    if cb:
        b.bind(on_release=lambda *_: cb())
    return b


def inp(hint, text="", multiline=False, h=44):
    return TextInput(hint_text=hint, text=text, multiline=multiline, size_hint_y=None,
                     height=dp(h if not multiline else 90), font_size=sp(15),
                     background_color=CARD, foreground_color=WHITE, cursor_color=WHITE,
                     hint_text_color=(0.6, 0.6, 0.65, 1), padding=(dp(10), dp(10)))


def spin(text, values):
    return Spinner(text=text, values=values, size_hint_y=None, height=dp(44),
                   background_normal="", background_color=GREY, font_size=sp(15))


def message(title, text):
    box = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(10))
    box.add_widget(lbl(text))
    p = Popup(title=title, content=box, size_hint=(0.9, None), height=dp(260))
    box.add_widget(btn("OK", p.dismiss))
    p.open()


def open_url(url, dial=False):
    if platform == "android":
        try:
            from jnius import autoclass
            Intent = autoclass("android.content.Intent")
            Uri = autoclass("android.net.Uri")
            act = autoclass("org.kivy.android.PythonActivity").mActivity
            intent = Intent(Intent.ACTION_DIAL if dial else Intent.ACTION_VIEW, Uri.parse(url))
            act.startActivity(intent)
            return
        except Exception:
            pass
    webbrowser.open(url)


def first_phone(s):
    p = (s or "").split(";")[0]
    return re.sub(r"[^\d+]", "", p)


def export_dir(app):
    if platform == "android":
        try:
            from android.storage import primary_external_storage_path
            d = os.path.join(primary_external_storage_path(), "Download")
            os.makedirs(d, exist_ok=True)
            t = os.path.join(d, ".w_test")
            open(t, "w").close(); os.remove(t)
            return d
        except Exception:
            return app.user_data_dir
    return os.path.expanduser("~")


class SearchScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.app = App.get_running_app()
        db = self.app.db
        self.stop_event = threading.Event()
        self.thread = None
        self.lines = []

        sv = ScrollView()
        col = GridLayout(cols=1, spacing=dp(8), padding=dp(12), size_hint_y=None)
        col.bind(minimum_height=col.setter("height"))
        col.add_widget(lbl("Поиск клиентов · Краснодар", 21, True))

        col.add_widget(lbl("Ключ API 2ГИС (бесплатный, см. инструкцию):", 13, color=(0.7, 0.75, 0.85, 1)))
        self.key = inp("Вставьте ключ", db.get_setting("api_key"))
        col.add_widget(self.key)

        col.add_widget(lbl("Что искать:", 15, True))
        self.checks = {}
        saved = db.get_setting("cats", "Кафе и рестораны,Кофейни,Барбершопы,Салоны красоты").split(",")
        for name in core.CATEGORIES:
            row = BoxLayout(size_hint_y=None, height=dp(34))
            cb = CheckBox(size_hint_x=None, width=dp(44), active=name in saved)
            row.add_widget(cb)
            row.add_widget(lbl(name, 15))
            self.checks[name] = cb
            col.add_widget(row)
        self.custom = inp("Своя категория (например: химчистка)")
        col.add_widget(self.custom)

        col.add_widget(lbl("Район:", 15, True))
        self.district = spin(db.get_setting("district", "Весь Краснодар"), core.DISTRICTS)
        col.add_widget(self.district)
        col.add_widget(lbl("Или свой адрес / место (тогда ищем рядом):", 15, True))
        self.address = inp("Например: ул. Красная, 100")
        col.add_widget(self.address)
        self.radius = spin("2 км", list(core.RADIUS))
        col.add_widget(self.radius)

        col.add_widget(lbl("Пороги для выделения:", 15, True))
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.few = inp("Мало отзывов ≤", db.get_setting("few", "20"))
        self.low = inp("Низкий рейтинг <", db.get_setting("low", "4.0"))
        row.add_widget(self.few); row.add_widget(self.low)
        col.add_widget(row)

        col.add_widget(btn("▶ Старт / продолжить", lambda: self.start(False), GREEN, 52))
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        row.add_widget(btn("⏸ Пауза", self.pause, RED))
        row.add_widget(btn("Поиск новых", lambda: self.start(True), ACCENT))
        col.add_widget(row)

        self.log_label = lbl("Журнал пуст", 13, color=(0.7, 0.8, 0.7, 1))
        col.add_widget(self.log_label)
        sv.add_widget(col)
        self.add_widget(sv)

    def log(self, msg):
        Clock.schedule_once(lambda dt: self._append(msg))

    def _append(self, msg):
        self.lines.append(datetime.now().strftime("%H:%M ") + msg)
        self.lines = self.lines[-40:]
        self.log_label.text = "\n".join(self.lines)

    def pause(self):
        self.stop_event.set()

    def start(self, fresh):
        if self.thread and self.thread.is_alive():
            self.log("Поиск уже идёт.")
            return
        key = self.key.text.strip()
        if not key:
            message("Нужен ключ", "Вставьте ключ API 2ГИС. Как его получить — в инструкции (README).")
            return
        queries = [(n, core.CATEGORIES[n]) for n, c in self.checks.items() if c.active]
        if self.custom.text.strip():
            queries.append((self.custom.text.strip(), [self.custom.text.strip()]))
        if not queries:
            message("Ничего не выбрано", "Отметьте хотя бы одну категорию.")
            return
        db = self.app.db
        db.set_setting("api_key", key)
        db.set_setting("cats", ",".join(n for n, c in self.checks.items() if c.active))
        db.set_setting("district", self.district.text)
        db.set_setting("few", self.few.text.strip() or "20")
        db.set_setting("low", self.low.text.strip() or "4.0")
        self.stop_event = threading.Event()
        args = (key, queries, self.district.text, self.address.text.strip(),
                core.RADIUS[self.radius.text], fresh)
        self.thread = threading.Thread(target=self._work, args=args, daemon=True)
        self.thread.start()

    def _work(self, key, queries, district, address, radius, fresh):
        try:
            s = core.Searcher(self.app.db, key, self.log)
            n = s.run(queries, district, address, radius, self.stop_event, fresh)
            self.log("Готово. Новых заведений: %d. Всего в базе: %d" % (n, self.app.db.count()))
        except core.ApiError as e:
            self.log("ОШИБКА: %s" % e)
        except Exception as e:
            self.log("Непредвиденная ошибка: %s" % e)


class LeadsScreen(Screen):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.app = App.get_running_app()
        root = BoxLayout(orientation="vertical", padding=dp(8), spacing=dp(6))
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.status = spin("Все", ["Все"] + core.STATUSES)
        self.mode = spin("Все", ["Все", "Мало отзывов", "Низкий рейтинг", "Не звонили"])
        row.add_widget(self.status); row.add_widget(self.mode)
        root.add_widget(row)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.text = inp("Поиск по названию/адресу")
        row.add_widget(self.text)
        row.add_widget(btn("Найти", self.refresh, ACCENT, 44))
        root.add_widget(row)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.count = lbl("", 14)
        row.add_widget(self.count)
        row.add_widget(btn("Excel (.xlsx)", self.export, GREEN, 44))
        root.add_widget(row)
        sv = ScrollView()
        self.grid = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        self.grid.bind(minimum_height=self.grid.setter("height"))
        sv.add_widget(self.grid)
        root.add_widget(sv)
        self.add_widget(root)

    def on_pre_enter(self, *a):
        self.refresh()

    def _num(self, key, default):
        try:
            return float(self.app.db.get_setting(key, str(default)).replace(",", "."))
        except ValueError:
            return default

    def refresh(self):
        few, low = int(self._num("few", 20)), self._num("low", 4.0)
        rows = self.app.db.query(self.status.text, self.mode.text, self.text.text.strip(), few, low)
        self.count.text = "Показано: %d из %d" % (len(rows), self.app.db.count())
        self.grid.clear_widgets()
        for l in rows:
            rating = ("%.1f" % l["rating"]) if l["rating"] is not None else "—"
            rev = l["reviews"] if l["reviews"] is not None else "—"
            tags = ""
            if l["reviews"] is not None and l["reviews"] <= few:
                tags += " [color=ffd24d]МАЛО ОТЗЫВОВ[/color]"
            if l["rating"] is not None and l["rating"] < low:
                tags += " [color=ff6b6b]НИЗКИЙ РЕЙТИНГ[/color]"
            txt = "[b]%s[/b]\n%s\n%s\nРейтинг: %s · Отзывов: %s · %s%s%s" % (
                l["name"], l["address"], l["phone"] or "нет телефона", rating, rev, l["status"],
                " · звонили" if l["called"] else "", tags)
            b = Button(text=txt, markup=True, size_hint_y=None, height=dp(112), font_size=sp(13),
                       halign="left", valign="middle", background_normal="",
                       background_color=STATUS_COLOR.get(l["status"], CARD))
            b.bind(size=lambda i, s: setattr(i, "text_size", (s[0] - dp(16), s[1])))
            b.bind(on_release=lambda _b, lead=l: self.edit(lead))
            self.grid.add_widget(b)

    def edit(self, lead):
        box = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(8))
        box.add_widget(lbl("%s\n%s" % (lead["name"], lead["address"]), 14))
        st = spin(lead["status"], core.STATUSES)
        box.add_widget(st)
        row = BoxLayout(size_hint_y=None, height=dp(38))
        called = CheckBox(size_hint_x=None, width=dp(44), active=bool(lead["called"]))
        row.add_widget(called); row.add_widget(lbl("Уже звонили", 15))
        box.add_widget(row)
        comment = inp("Комментарий", lead["comment"] or "", True)
        box.add_widget(comment)
        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(4))
        if lead["phone"]:
            row.add_widget(btn("Позвонить", lambda: open_url("tel:" + first_phone(lead["phone"]), True), GREEN, 44))
        row.add_widget(btn("Я.Карты", lambda: open_url(lead["yandex_url"]), GREY, 44))
        row.add_widget(btn("2ГИС", lambda: open_url(lead["gis_url"]), GREY, 44))
        if lead["site"]:
            row.add_widget(btn("Сайт", lambda: open_url(lead["site"]), GREY, 44))
        box.add_widget(row)
        p = Popup(title="Заведение", content=box, size_hint=(0.95, 0.92))

        def save():
            if st.text != "Новый" and not called.active and st.text == "Позвонил":
                called.active = True
            self.app.db.update_lead(lead["id"], st.text, called.active, comment.text.strip())
            p.dismiss()
            self.refresh()
        box.add_widget(btn("Сохранить", save, ACCENT, 48))
        p.open()

    def export(self):
        try:
            d = export_dir(self.app)
            path = os.path.join(d, "klienty_%s.xlsx" % datetime.now().strftime("%Y%m%d_%H%M"))
            core.export_xlsx(path, self.app.db.all_leads())
            message("Готово", "Файл Excel сохранён:\n%s" % path)
        except Exception as e:
            message("Ошибка экспорта", str(e))


class LeadsApp(App):
    title = "NFC Leads"

    def build(self):
        Window.clearcolor = BG
        if platform == "android":
            try:
                from android.permissions import Permission, request_permissions
                request_permissions([Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE])
            except Exception:
                pass
        self.db = core.Database(os.path.join(self.user_data_dir, "leads.db"))
        root = BoxLayout(orientation="vertical")
        self.sm = ScreenManager()
        self.sm.add_widget(SearchScreen(name="search"))
        self.sm.add_widget(LeadsScreen(name="leads"))
        root.add_widget(self.sm)
        nav = BoxLayout(size_hint_y=None, height=dp(52))
        nav.add_widget(btn("Поиск", lambda: setattr(self.sm, "current", "search"), ACCENT, 52))
        nav.add_widget(btn("База клиентов", lambda: setattr(self.sm, "current", "leads"), GREEN, 52))
        root.add_widget(nav)
        return root


if __name__ == "__main__":
    LeadsApp().run()
