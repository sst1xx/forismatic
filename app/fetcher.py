"""
Fetcher: загружает русскоязычные цитаты и афоризмы.

Запускается как отдельный OS-процесс (python -m app.fetcher).
Полностью синхронный — без asyncio, без httpx, без тредов.
Использует только stdlib: urllib.request, sqlite3, json, re.
"""

import re
import json
import time
import logging
import sqlite3
import urllib.request
import urllib.parse
import os
from typing import Optional
from bs4 import BeautifulSoup

DB_PATH = os.environ.get("DB_PATH", "/data/quotes.db")
INSERT_BATCH = 200
MAX_RESPONSE_BYTES = 5_000_000
TARGET = 20_000
BATCH = 3_000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

UA = "forismatic-bot/1.0 (https://github.com/forismatic; educational project)"


# ---------------------------------------------------------------------------
# DB (синхронный, без тредов)
# ---------------------------------------------------------------------------

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def db_init():
    conn = db_connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            author TEXT
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_text ON quotes(text)")
    conn.commit()
    conn.close()


def db_count() -> int:
    conn = db_connect()
    row = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()
    conn.close()
    return row[0] if row else 0


def db_insert(quotes: list):
    if not quotes:
        return
    conn = db_connect()
    for i in range(0, len(quotes), INSERT_BATCH):
        conn.executemany(
            "INSERT OR IGNORE INTO quotes (text, author) VALUES (?, ?)",
            quotes[i:i + INSERT_BATCH],
        )
        conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# HTTP (синхронный, без тредов — только urllib.request)
# ---------------------------------------------------------------------------

def http_get(url: str, params: dict = None, timeout: int = 20) -> Optional[bytes]:
    """GET через urllib — никаких тредов."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(MAX_RESPONSE_BYTES)
    except Exception as e:
        log.warning(f"HTTP error {url}: {e}")
        return None


def http_get_json(url: str, params: dict = None) -> dict:
    data = http_get(url, params)
    if not data:
        return {}
    try:
        return json.loads(data.decode("utf-8"))
    except Exception as e:
        log.warning(f"JSON decode error: {e}")
        return {}


# ---------------------------------------------------------------------------
# Утилиты парсинга
# ---------------------------------------------------------------------------

def clean_text(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = re.sub(r"\s*[\u2014\u2013\u2012]\s*", " - ", s)
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r" +([.!?,;:])", r"\1", s)
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    return s.strip()


def ensure_punct(text: str) -> str:
    if not text:
        return text
    if text[-1] not in ".!?…":
        text += "."
    return text


def is_valid(text: str) -> bool:
    if len(text) < 20 or len(text) > 500:
        return False
    if not re.search(r"[а-яёА-ЯЁ]", text):
        return False
    if text.startswith("[[") or text.startswith("{{"):
        return False
    nav_prefixes = (":", "категория:", "см.", "см. также", "wikiquote")
    if text.lower().startswith(nav_prefixes):
        return False
    if not re.search(r"[а-яёА-ЯЁ]{4,}", text):
        return False
    if len(text.split()) < 4:
        return False
    return True


# ---------------------------------------------------------------------------
# Источник 1: WikiQuote RU
# ---------------------------------------------------------------------------

WIKI_API = "https://ru.wikiquote.org/w/api.php"

WIKIAUTHORS = [
    "Александр Пушкин", "Лев Толстой", "Фёдор Достоевский", "Антон Чехов",
    "Михаил Булгаков", "Максим Горький", "Иван Тургенев", "Николай Гоголь",
    "Михаил Лермонтов", "Иван Бунин", "Анна Ахматова", "Борис Пастернак",
    "Марина Цветаева", "Сергей Есенин", "Владимир Маяковский", "Александр Блок",
    "Иосиф Бродский", "Александр Солженицын", "Михаил Зощенко", "Илья Ильф",
    "Евгений Петров", "Аркадий Аверченко", "Саша Чёрный", "Тэффи",
    "Козьма Прутков", "Уинстон Черчилль", "Марк Твен", "Оскар Уайльд",
    "Бернард Шоу", "Альберт Эйнштейн", "Зигмунд Фрейд", "Карл Маркс",
    "Фридрих Ницше", "Иммануил Кант", "Артур Шопенгауэр", "Вольтер",
    "Жан-Жак Руссо", "Дени Дидро", "Монтескьё", "Блез Паскаль",
    "Рене Декарт", "Фрэнсис Бэкон", "Джон Локк", "Томас Гоббс",
    "Конфуций", "Лао-цзы", "Сократ", "Платон", "Аристотель",
    "Цицерон", "Марк Аврелий", "Сенека", "Плутарх", "Эпиктет",
    "Гераклит", "Демокрит", "Пифагор", "Диоген Синопский",
    "Наполеон Бонапарт", "Авраам Линкольн", "Уильям Шекспир",
    "Мигель де Сервантес", "Данте Алигьери", "Иоганн Гёте",
    "Фридрих Шиллер", "Генрик Ибсен", "Ханс Кристиан Андерсен",
    "Эрнест Хемингуэй", "Фрэнсис Скотт Фицджеральд", "Джек Лондон",
    "Оноре де Бальзак", "Стендаль", "Гюстав Флобер", "Виктор Гюго",
    "Александр Дюма", "Жюль Верн", "Антуан де Сент-Экзюпери",
    "Альбер Камю", "Жан-Поль Сартр", "Симона де Бовуар",
    "Николай Некрасов", "Александр Герцен", "Виссарион Белинский",
    "Николай Чернышевский", "Дмитрий Писарев",
]


def fetch_wikiquote_author(author: str) -> list:
    data = http_get_json(WIKI_API, {
        "action": "parse", "page": author,
        "prop": "wikitext", "format": "json", "redirects": 1,
    })
    if "error" in data or "parse" not in data:
        return []
    results = []
    wikitext = data["parse"]["wikitext"]["*"]
    for line in wikitext.split("\n"):
        m = re.match(r"^\*+\s*(.+)$", line)
        if not m:
            continue
        raw = m.group(1)
        if re.search(r"\[https?://", raw):
            continue
        raw = re.sub(r"<ref[^>]*>.*?</ref>", "", raw, flags=re.DOTALL)
        raw = re.sub(r"<[^>]+>", "", raw)
        raw = re.sub(r"\{\{[^}]*\}\}", "", raw)
        raw = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", raw)
        raw = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", raw)
        raw = re.sub(r"\[https?://\S+\]", "", raw)
        raw = re.sub(r"'+", "", raw)
        raw = clean_text(raw)
        raw = ensure_punct(raw)
        if is_valid(raw):
            results.append((raw, author))
    return results


def fetch_wikiquote(target: int = 3000) -> list:
    log.info("=== WikiQuote RU ===")
    all_quotes = []
    for i, author in enumerate(WIKIAUTHORS):
        quotes = fetch_wikiquote_author(author)
        all_quotes.extend(quotes)
        log.info(f"  [{i+1}/{len(WIKIAUTHORS)}] {author}: {len(quotes)} цитат (всего: {len(all_quotes)})")
        if len(all_quotes) >= target:
            break
        time.sleep(0.3)
    log.info(f"WikiQuote: итого {len(all_quotes)} цитат")
    return all_quotes


# ---------------------------------------------------------------------------
# Источник 2: aphorism.ru
# ---------------------------------------------------------------------------
# Стратегия: /today/ + архив по дням /archive/YYYY/M/D/
# Кодировка: Windows-1251
# Селекторы: a[href*="/comments/"] — текст, a[href*="/author/"] — автор
# ---------------------------------------------------------------------------

APHORISM_RU_BASE = "https://aphorism.ru"


def fetch_aphorism_page(url: str) -> list:
    data = http_get(url)
    if not data:
        return []
    try:
        html = data.decode("cp1251", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        results = []
        seen_texts = set()
        for a_quote in soup.select('a[href*="/comments/"]'):
            text = clean_text(a_quote.get_text(strip=True))
            text = ensure_punct(text)
            if not is_valid(text) or text in seen_texts:
                continue
            seen_texts.add(text)
            author = None
            # автор — ближайший <a href*="/author/"> рядом
            author_el = a_quote.find_next_sibling("a")
            if author_el and "/author/" in author_el.get("href", ""):
                a_text = clean_text(author_el.get_text(strip=True))
                if a_text and len(a_text) <= 60:
                    author = a_text
            if author is None:
                parent = a_quote.parent
                if parent:
                    author_el = parent.find("a", href=lambda h: h and "/author/" in h)
                    if author_el:
                        a_text = clean_text(author_el.get_text(strip=True))
                        if a_text and len(a_text) <= 60:
                            author = a_text
            results.append((text, author))
        return results
    except Exception as e:
        log.warning(f"aphorism.ru parse error {url}: {e}")
        return []


def fetch_aphorism_ru(max_quotes: int = 3000) -> list:
    import datetime as dt
    log.info("=== aphorism.ru ===")
    all_quotes: list = []

    # сначала /today/
    quotes = fetch_aphorism_page(f"{APHORISM_RU_BASE}/today/")
    if quotes:
        all_quotes.extend(quotes)
        log.info(f"  /today/: {len(quotes)} цитат (всего: {len(all_quotes)})")
    time.sleep(0.5)

    # потом архив: идём назад по дням
    today = dt.date.today()
    day = today - dt.timedelta(days=1)
    while len(all_quotes) < max_quotes:
        url = f"{APHORISM_RU_BASE}/archive/{day.year}/{day.month}/{day.day}/"
        quotes = fetch_aphorism_page(url)
        if quotes:
            all_quotes.extend(quotes)
            log.info(f"  {url}: {len(quotes)} цитат (всего: {len(all_quotes)})")
        else:
            log.info(f"  {url}: нет цитат, пропускаем")
        day -= dt.timedelta(days=1)
        # не уходим глубже 2 лет
        if (today - day).days > 730:
            break
        time.sleep(0.4)

    log.info(f"aphorism.ru: итого {len(all_quotes)} цитат")
    return all_quotes


# ---------------------------------------------------------------------------
# Источник 3: citaty.info
# ---------------------------------------------------------------------------
# Стратегия: листинг /man?page=N (0-indexed)
# Кодировка: UTF-8
# Селекторы: a[href*="/quote/"] — текст, a[title="Автор цитаты"] — автор
# ---------------------------------------------------------------------------

CITATY_BASE = "https://citaty.info"


def fetch_citaty_page(url: str) -> list:
    data = http_get(url)
    if not data:
        return []
    try:
        soup = BeautifulSoup(data.decode("utf-8", errors="replace"), "html.parser")
        results = []
        seen_texts = set()
        for a_quote in soup.select('a[href*="/quote/"]'):
            text = clean_text(a_quote.get_text(strip=True))
            text = ensure_punct(text)
            if not is_valid(text) or text in seen_texts:
                continue
            seen_texts.add(text)
            author = None
            author_el = a_quote.find_next_sibling("a", title="Автор цитаты")
            if author_el:
                a_text = clean_text(author_el.get_text(strip=True))
                if a_text and len(a_text) <= 60:
                    author = a_text
            results.append((text, author))
        return results
    except Exception as e:
        log.warning(f"citaty.info parse error {url}: {e}")
        return []


def fetch_citaty_info(max_pages: int = 150) -> list:
    log.info("=== citaty.info ===")
    all_quotes: list = []
    for page in range(max_pages):
        url = f"{CITATY_BASE}/man" + (f"?page={page}" if page > 0 else "")
        quotes = fetch_citaty_page(url)
        if not quotes:
            log.info(f"  {url}: нет цитат, останавливаемся")
            break
        all_quotes.extend(quotes)
        log.info(f"  {url}: {len(quotes)} цитат (всего: {len(all_quotes)})")
        time.sleep(0.5)
        if len(all_quotes) >= 3000:
            break
    log.info(f"citaty.info: итого {len(all_quotes)} цитат")
    return all_quotes


# ---------------------------------------------------------------------------
# Источник 4: Встроенная база
# ---------------------------------------------------------------------------

BUILTIN_QUOTES = [
    ("Краткость - сестра таланта.", "Антон Чехов"),
    ("Все счастливые семьи похожи друг на друга, каждая несчастливая семья несчастна по-своему.", "Лев Толстой"),
    ("Красота спасёт мир.", "Фёдор Достоевский"),
    ("Человек - это звучит гордо.", "Максим Горький"),
    ("Рукописи не горят.", "Михаил Булгаков"),
    ("Любить - это значит жить жизнью того, кого любишь.", "Лев Толстой"),
    ("Счастье не в том, чтобы делать всегда то, что хочешь, а в том, чтобы всегда хотеть того, что делаешь.", "Лев Толстой"),
    ("Если хочешь быть счастливым, будь им.", "Козьма Прутков"),
    ("Нельзя объять необъятное.", "Козьма Прутков"),
    ("Смотри в корень!", "Козьма Прутков"),
    ("Бывают времена, когда нельзя жить, не согрешив.", "Фёдор Достоевский"),
    ("Жить нужно так, чтобы было не стыдно вспомнить прошлое.", "Фёдор Достоевский"),
    ("Надо любить жизнь больше, чем смысл жизни.", "Фёдор Достоевский"),
    ("Умный человек не тот, кто много знает, а тот, кто знает самого себя.", "Иоганн Гёте"),
    ("Мы все учились понемногу чему-нибудь и как-нибудь.", "Александр Пушкин"),
    ("Любви все возрасты покорны.", "Александр Пушкин"),
    ("Привычка свыше нам дана, замена счастию она.", "Александр Пушкин"),
    ("Жизнь прожить - не поле перейти.", "Борис Пастернак"),
    ("Трус умирает много раз, храбрец умирает один раз.", "Уильям Шекспир"),
    ("Весь мир - театр. В нём женщины, мужчины - все актёры.", "Уильям Шекспир"),
    ("Быть или не быть - вот в чём вопрос.", "Уильям Шекспир"),
    ("Тот, кто не ценит своей жизни, не заслуживает её.", "Леонардо да Винчи"),
    ("Простота - это высшая степень искусства.", "Леонардо да Винчи"),
    ("Единственная настоящая ошибка - не исправлять своих прошлых ошибок.", "Конфуций"),
    ("Не важно, насколько медленно ты движешься, главное - не останавливаться.", "Конфуций"),
    ("Выберете работу по душе, и вам не придётся работать ни одного дня.", "Конфуций"),
    ("Знание - сила.", "Фрэнсис Бэкон"),
    ("Мыслю - следовательно, существую.", "Рене Декарт"),
    ("Человек рождён свободным, а между тем везде он в оковах.", "Жан-Жак Руссо"),
    ("В этом мире непреложны лишь смерть и налоги.", "Бенджамин Франклин"),
    ("Жизнь - это то, что происходит с вами, пока вы строите другие планы.", "Джон Леннон"),
    ("Воображение важнее знания.", "Альберт Эйнштейн"),
    ("Есть только два способа прожить жизнь. Первый - так, будто никаких чудес не бывает. Второй - так, будто всё является чудом.", "Альберт Эйнштейн"),
    ("Если вы не можете объяснить что-то просто, вы недостаточно это понимаете.", "Альберт Эйнштейн"),
    ("То, что нас не убивает, делает нас сильнее.", "Фридрих Ницше"),
    ("Без музыки жизнь была бы ошибкой.", "Фридрих Ницше"),
    ("Дорогу осилит идущий.", None),
    ("Лучше зажечь свечу, чем проклинать темноту.", "Конфуций"),
    ("Будь изменением, которое ты хочешь увидеть в мире.", "Махатма Ганди"),
    ("Слабый никогда не сможет простить. Прощение - это атрибут сильного.", "Махатма Ганди"),
    ("Сила не в теле, а в душе.", "Лев Толстой"),
    ("Человек, который никогда не ошибался, никогда не пробовал ничего нового.", "Альберт Эйнштейн"),
    ("Я знаю, что я ничего не знаю.", "Сократ"),
    ("Жизнь подобна ехать на велосипеде. Чтобы сохранить равновесие, нужно двигаться.", "Альберт Эйнштейн"),
    ("Вдохновение существует, но оно должно застать тебя за работой.", "Пабло Пикассо"),
    ("Лучше поздно, чем никогда.", None),
    ("Всё гениальное - просто.", None),
    ("Платон мне друг, но истина дороже.", "Аристотель"),
    ("Пришёл, увидел, победил.", "Юлий Цезарь"),
    ("Кто предупреждён - тот вооружён.", None),
    ("Никогда не прерывайте врага, когда он делает ошибку.", "Наполеон Бонапарт"),
    ("Невозможное - это просто слово из словаря дураков.", "Наполеон Бонапарт"),
    ("Слухи о моей смерти сильно преувеличены.", "Марк Твен"),
    ("Классик - это книга, которую все хвалят и никто не читает.", "Марк Твен"),
    ("Никогда не сдавайтесь!", "Уинстон Черчилль"),
    ("Успех - это способность идти от неудачи к неудаче, не теряя энтузиазма.", "Уинстон Черчилль"),
]


# ---------------------------------------------------------------------------
# Главная точка входа
# ---------------------------------------------------------------------------

def run_fetch():
    db_init()

    current = db_count()
    batch_target = min(current + BATCH, TARGET)
    log.info(f"Старт fetcher. В базе: {current}, цель этого запуска: {batch_target} (макс: {TARGET}).")

    current = db_count()
    if current >= batch_target:
        log.info(f"База содержит {current} записей, цель {batch_target} достигнута.")
        return

    log.info(f"Досгружаем до {batch_target}. Сейчас в базе: {current}")

    db_insert(BUILTIN_QUOTES)
    current = db_count()
    log.info(f"После встроенной базы: {current} записей")

    if current < batch_target:
        wiki_quotes = fetch_wikiquote(target=batch_target - current + 500)
        if wiki_quotes:
            db_insert(wiki_quotes)
            current = db_count()
            log.info(f"После WikiQuote: {current} записей")

    if current < batch_target:
        aph = fetch_aphorism_ru()
        if aph:
            db_insert(aph)
            current = db_count()
            log.info(f"После aphorism.ru: {current} записей")

    if current < batch_target:
        cit = fetch_citaty_info()
        if cit:
            db_insert(cit)
            current = db_count()
            log.info(f"После citaty.info: {current} записей")

    log.info(f"Загрузка завершена. Итого в базе: {db_count()} записей.")


if __name__ == "__main__":
    run_fetch()
