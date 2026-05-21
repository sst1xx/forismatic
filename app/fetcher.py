"""
Fetcher: загружает 5000+ русскоязычных цитат, афоризмов и интересных фактов.

Источники:
1. WikiQuote RU (через Wikimedia API) - цитаты знаменитых людей
2. Wikipedia RU "Знаете ли вы" (через Wikimedia API) - кураторские факты
3. aphorism.ru - большая база афоризмов
4. citaty.info - цитаты по темам
5. Встроенная база (fallback) - 100+ классических цитат

Запуск:
    python -m app.fetcher
или автоматически при старте если БД пустая.
"""

import asyncio
import re
import sys
import logging
from bs4 import BeautifulSoup
import httpx

from .database import init_db, insert_quotes, count_quotes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "forismatic-bot/1.0 (https://github.com/forismatic; educational project)"
}

# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def clean_text(s: str) -> str:
    """Убрать лишние пробелы, спецсимволы, длинные тире -> обычный дефис."""
    if not s:
        return ""
    s = s.strip()
    # Длинные тире и похожие символы -> обычный дефис с пробелами
    s = re.sub(r"\s*[\u2014\u2013\u2012]\s*", " - ", s)
    # Несколько пробелов -> один
    s = re.sub(r" {2,}", " ", s)
    # Пробел перед знаком препинания (артефакт удалённых шаблонов)
    s = re.sub(r" +([.!?,;:])", r"\1", s)
    # Убрать управляющие символы кроме обычного пробела
    s = re.sub(r"[\x00-\x1f\x7f]", "", s)
    return s.strip()


def ensure_punct(text: str) -> str:
    """Убедиться что цитата заканчивается знаком препинания."""
    if not text:
        return text
    if text[-1] not in ".!?…":
        text += "."
    return text


def is_valid(text: str) -> bool:
    """Фильтрация мусора: минимальная длина, наличие кириллицы."""
    if len(text) < 20 or len(text) > 500:
        return False
    # Должна содержать кириллицу
    if not re.search(r"[а-яёА-ЯЁ]", text):
        return False
    # Не должна быть просто шаблоном [[Category:...]] или подобным
    if text.startswith("[[") or text.startswith("{{"):
        return False
    # Навигационные строки WikiQuote (категории, технические пометки)
    nav_prefixes = (":", "категория:", "см.", "см. также", "wikiquote")
    if text.lower().startswith(nav_prefixes):
        return False
    # Должна содержать хотя бы одно слово длиннее 3 символов из кириллицы
    if not re.search(r"[а-яёА-ЯЁ]{4,}", text):
        return False
    # Минимум 4 слова (заголовки разделов типа "Критика и публицистика" не пройдут)
    if len(text.split()) < 4:
        return False
    return True


# ---------------------------------------------------------------------------
# Источник 1: WikiQuote RU — через MediaWiki API
# ---------------------------------------------------------------------------

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

WIKI_API = "https://ru.wikiquote.org/w/api.php"


async def fetch_wikiquote_author(client: httpx.AsyncClient, author: str) -> list[tuple[str, str | None]]:
    results = []
    try:
        resp = await client.get(WIKI_API, params={
            "action": "parse",
            "page": author,
            "prop": "wikitext",
            "format": "json",
            "redirects": 1,
        }, timeout=20)
        if resp.status_code != 200:
            return results
        data = resp.json()
        if "error" in data or "parse" not in data:
            return results

        wikitext = data["parse"]["wikitext"]["*"]
        lines = wikitext.split("\n")
        for line in lines:
            # Цитаты обычно начинаются с * или ** в викитексте
            m = re.match(r"^\*+\s*(.+)$", line)
            if not m:
                continue
            raw = m.group(1)
            # Пропустить строки с внешними ссылками (раздел "Ссылки")
            if re.search(r"\[https?://", raw):
                continue
            # Убрать вики-разметку: [[...]], {{...}}, ''...'', <ref>...</ref>
            raw = re.sub(r"<ref[^>]*>.*?</ref>", "", raw, flags=re.DOTALL)
            raw = re.sub(r"<[^>]+>", "", raw)
            raw = re.sub(r"\{\{[^}]*\}\}", "", raw)
            raw = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", raw)
            # Внешние ссылки без протокола или оставшиеся [url текст] → текст
            raw = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", raw)
            raw = re.sub(r"\[https?://\S+\]", "", raw)
            raw = re.sub(r"'+", "", raw)
            raw = clean_text(raw)
            raw = ensure_punct(raw)
            if is_valid(raw):
                results.append((raw, author))
    except Exception as e:
        log.warning(f"WikiQuote error for {author}: {e}")
    return results


async def fetch_wikiquote(target: int = 3000) -> list[tuple[str, str | None]]:
    log.info("=== WikiQuote RU ===")
    all_quotes: list[tuple[str, str | None]] = []
    async with httpx.AsyncClient(headers=HEADERS) as client:
        for i, author in enumerate(WIKIAUTHORS):
            quotes = await fetch_wikiquote_author(client, author)
            all_quotes.extend(quotes)
            log.info(f"  [{i+1}/{len(WIKIAUTHORS)}] {author}: {len(quotes)} цитат (всего: {len(all_quotes)})")
            if len(all_quotes) >= target:
                break
            await asyncio.sleep(0.3)
    log.info(f"WikiQuote: итого {len(all_quotes)} цитат")
    return all_quotes


# ---------------------------------------------------------------------------
# Источник 2: Wikipedia RU — "Знаете ли вы" (кураторские факты)
# ---------------------------------------------------------------------------

WIKI_API_RU = "https://ru.wikipedia.org/w/api.php"


def parse_did_you_know_wikitext(wikitext: str) -> list[str]:
    """
    Извлечь факты из wikitext страницы "Знаете ли вы".
    Факты — строки начинающиеся с "* ".
    """
    facts = []
    for line in wikitext.split("\n"):
        m = re.match(r"^\*\s+(.+)$", line)
        if not m:
            continue
        raw = m.group(1)

        # Убрать [[Файл:...]] и [[File:...]] (иллюстрации в строке)
        raw = re.sub(r"\[\[(?:Файл|File|Image|Изображение):[^\]]*\]\]", "", raw, flags=re.IGNORECASE)
        # [[Ссылка|Текст]] → Текст
        raw = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", raw)
        # '''жирный''' и ''курсив'' → текст
        raw = re.sub(r"'{2,3}", "", raw)
        # {{шаблон|...}} → убрать
        raw = re.sub(r"\{\{[^}]*\}\}", "", raw)
        # <ref>...</ref> и <ref ... />
        raw = re.sub(r"<ref[^>]*/?>.*?</ref>", "", raw, flags=re.DOTALL)
        raw = re.sub(r"<ref[^>]*/?>", "", raw)
        # HTML-теги
        raw = re.sub(r"<[^>]+>", "", raw)

        raw = clean_text(raw)
        raw = ensure_punct(raw)

        if is_valid(raw):
            facts.append(raw)
    return facts


async def fetch_did_you_know_month(
    client: httpx.AsyncClient, year: int, month: int
) -> list[str]:
    """Загрузить факты ЗЛВ за один месяц из архива."""
    page = f"Проект:Знаете_ли_вы/Архив_рубрики/{year}-{month:02d}"
    try:
        resp = await client.get(WIKI_API_RU, params={
            "action": "parse",
            "page": page,
            "prop": "wikitext",
            "format": "json",
        }, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if "error" in data or "parse" not in data:
            return []
        wikitext = data["parse"]["wikitext"]["*"]
        return parse_did_you_know_wikitext(wikitext)
    except Exception as e:
        log.warning(f"ЗЛВ архив {year}-{month:02d}: {e}")
        return []


async def fetch_did_you_know_current(client: httpx.AsyncClient) -> list[str]:
    """Загрузить текущий шаблон ЗЛВ (8-12 актуальных фактов)."""
    try:
        resp = await client.get(WIKI_API_RU, params={
            "action": "parse",
            "page": "Шаблон:Знаете_ли_вы",
            "prop": "wikitext",
            "format": "json",
        }, timeout=20)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if "error" in data or "parse" not in data:
            return []
        wikitext = data["parse"]["wikitext"]["*"]
        return parse_did_you_know_wikitext(wikitext)
    except Exception as e:
        log.warning(f"ЗЛВ текущий шаблон: {e}")
        return []


async def fetch_wikipedia_did_you_know(target: int = 2000) -> list[tuple[str, None]]:
    """
    Загрузить кураторские факты "Знаете ли вы" из русской Википедии.
    Сначала текущий шаблон, затем архив по месяцам от текущего назад.
    Возвращает list[(text, None)] — автор у фактов ЗЛВ не указывается.
    """
    import datetime
    log.info("=== Wikipedia RU 'Знаете ли вы' ===")
    all_facts: list[str] = []

    async with httpx.AsyncClient(headers=HEADERS) as client:
        # Текущий шаблон
        current_facts = await fetch_did_you_know_current(client)
        all_facts.extend(current_facts)
        log.info(f"  Текущий шаблон: {len(current_facts)} фактов")
        await asyncio.sleep(0.5)

        # Архив по месяцам от текущего назад
        now = datetime.date.today()
        year, month = now.year, now.month

        while len(all_facts) < target:
            # Переходим к предыдущему месяцу
            month -= 1
            if month == 0:
                month = 12
                year -= 1
            # Архив доступен с января 2008
            if year < 2008:
                break

            facts = await fetch_did_you_know_month(client, year, month)
            all_facts.extend(facts)
            log.info(
                f"  Архив {year}-{month:02d}: {len(facts)} фактов "
                f"(всего: {len(all_facts)})"
            )
            await asyncio.sleep(0.5)

    # Дедупликация внутри списка (по тексту)
    seen: set[str] = set()
    unique: list[str] = []
    for f in all_facts:
        if f not in seen:
            seen.add(f)
            unique.append(f)

    result = [(text, None) for text in unique[:target]]
    log.info(f"Wikipedia ЗЛВ: итого {len(result)} фактов")
    return result


# ---------------------------------------------------------------------------
# Источник 3: aphorism.ru
# ---------------------------------------------------------------------------

APHORISM_RU_BASE = "https://aphorism.ru"

APHORISM_CATEGORIES = [
    "/aphorism/", "/proverb/", "/quote/", "/humor/",
    "/aphorism/love/", "/aphorism/life/", "/aphorism/wisdom/",
    "/aphorism/friendship/", "/aphorism/happiness/", "/aphorism/work/",
    "/aphorism/time/", "/aphorism/money/", "/aphorism/mind/",
    "/aphorism/woman/", "/aphorism/man/", "/aphorism/book/",
]


async def fetch_aphorism_page(client: httpx.AsyncClient, url: str) -> list[tuple[str, str | None]]:
    results = []
    try:
        resp = await client.get(url, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return results
        soup = BeautifulSoup(resp.text, "lxml")

        # Структура aphorism.ru: цитата в div.aphorism-text, автор в div.aphorism-author
        for block in soup.select("div.aphorism-text, .quote-text, .aph-text, [class*='aphorism']"):
            text_el = block.get_text(strip=True)
            text = clean_text(text_el)
            text = ensure_punct(text)
            if not is_valid(text):
                continue
            # Пробуем найти автора рядом
            author = None
            parent = block.parent
            if parent:
                author_el = parent.select_one("[class*='author'], [class*='name']")
                if author_el:
                    author = clean_text(author_el.get_text(strip=True))
                    if len(author) > 60 or not author:
                        author = None
            results.append((text, author))
    except Exception as e:
        log.warning(f"aphorism.ru error {url}: {e}")
    return results


async def fetch_aphorism_ru(max_pages: int = 50) -> list[tuple[str, str | None]]:
    log.info("=== aphorism.ru ===")
    all_quotes: list[tuple[str, str | None]] = []
    async with httpx.AsyncClient(headers=HEADERS) as client:
        for cat in APHORISM_CATEGORIES:
            for page in range(1, max_pages + 1):
                url = f"{APHORISM_RU_BASE}{cat}?page={page}" if page > 1 else f"{APHORISM_RU_BASE}{cat}"
                quotes = await fetch_aphorism_page(client, url)
                if not quotes:
                    break
                all_quotes.extend(quotes)
                log.info(f"  {url}: {len(quotes)} цитат (всего: {len(all_quotes)})")
                await asyncio.sleep(0.5)
                if len(all_quotes) >= 3000:
                    break
            if len(all_quotes) >= 3000:
                break
    log.info(f"aphorism.ru: итого {len(all_quotes)} цитат")
    return all_quotes


# ---------------------------------------------------------------------------
# Источник 3: citaty.info
# ---------------------------------------------------------------------------

CITATY_BASE = "https://citaty.info"

CITATY_SECTIONS = [
    "/citaty/", "/aforizmy/", "/poslovicy/", "/vyskazyvaniya/",
    "/citaty/o-zhizni/", "/citaty/o-lyubvi/", "/citaty/o-schaste/",
    "/citaty/o-vremeni/", "/citaty/o-druge/", "/citaty/o-mudrosti/",
    "/citaty/o-sile/", "/citaty/o-trude/", "/citaty/o-knigah/",
]


async def fetch_citaty_page(client: httpx.AsyncClient, url: str) -> list[tuple[str, str | None]]:
    results = []
    try:
        resp = await client.get(url, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return results
        soup = BeautifulSoup(resp.text, "lxml")

        for block in soup.select(".quote, .quote-body, [class*='quote'], article"):
            text_el = block.select_one("p, .text, [class*='text'], [class*='body']")
            if not text_el:
                text_el = block
            text = clean_text(text_el.get_text(strip=True))
            text = ensure_punct(text)
            if not is_valid(text):
                continue
            author = None
            author_el = block.select_one("[class*='author'], [class*='name'], cite, footer")
            if author_el:
                author = clean_text(author_el.get_text(strip=True))
                if len(author) > 60 or not author:
                    author = None
            results.append((text, author))
    except Exception as e:
        log.warning(f"citaty.info error {url}: {e}")
    return results


async def fetch_citaty_info(max_pages: int = 30) -> list[tuple[str, str | None]]:
    log.info("=== citaty.info ===")
    all_quotes: list[tuple[str, str | None]] = []
    async with httpx.AsyncClient(headers=HEADERS) as client:
        for section in CITATY_SECTIONS:
            for page in range(1, max_pages + 1):
                url = f"{CITATY_BASE}{section}?page={page}" if page > 1 else f"{CITATY_BASE}{section}"
                quotes = await fetch_citaty_page(client, url)
                if not quotes:
                    break
                all_quotes.extend(quotes)
                log.info(f"  {url}: {len(quotes)} цитат (всего: {len(all_quotes)})")
                await asyncio.sleep(0.5)
                if len(all_quotes) >= 2000:
                    break
            if len(all_quotes) >= 2000:
                break
    log.info(f"citaty.info: итого {len(all_quotes)} цитат")
    return all_quotes


# ---------------------------------------------------------------------------
# Источник 4: Встроенная база (fallback / стартовая база)
# ---------------------------------------------------------------------------

BUILTIN_QUOTES: list[tuple[str, str | None]] = [
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
    ("Не бойся врагов - в худшем случае они могут тебя убить. Не бойся друзей - в худшем случае они могут тебя предать. Бойся равнодушных - они не убивают и не предают, но только с их молчаливого согласия существует на земле предательство и убийство.", "Бруно Ясенский"),
    ("Умный человек не тот, кто много знает, а тот, кто знает самого себя.", "Иоганн Гёте"),
    ("Мы все учились понемногу чему-нибудь и как-нибудь.", "Александр Пушкин"),
    ("Чем ночь темней, тем ярче звёзды.", "Аполлон Майков"),
    ("Я памятник себе воздвиг нерукотворный.", "Александр Пушкин"),
    ("Любви все возрасты покорны.", "Александр Пушкин"),
    ("Привычка свыше нам дана, замена счастию она.", "Александр Пушкин"),
    ("Жизнь прожить - не поле перейти.", "Борис Пастернак"),
    ("Доктор, у вас нет сердца. - Нет, оно есть. Просто в нём нет тебя.", "Михаил Булгаков"),
    ("Трус умирает много раз, храбрец умирает один раз.", "Уильям Шекспир"),
    ("Весь мир - театр. В нём женщины, мужчины - все актёры.", "Уильям Шекспир"),
    ("Быть или не быть - вот в чём вопрос.", "Уильям Шекспир"),
    ("Тот, кто не ценит своей жизни, не заслуживает её.", "Леонардо да Винчи"),
    ("Опыт не делает ошибок.", "Леонардо да Винчи"),
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
    ("Жизнь - это 10% того, что с тобой происходит, и 90% - как ты на это реагируешь.", "Чарльз Суиндолл"),
    ("Ничто великое в мире не совершалось без страсти.", "Георг Вильгельм Гегель"),
    ("То, что нас не убивает, делает нас сильнее.", "Фридрих Ницше"),
    ("Без музыки жизнь была бы ошибкой.", "Фридрих Ницше"),
    ("Чем глубже яма, тем выше куча.", None),
    ("Человек - единственное существо, которое смеётся, так как только он один видит разницу между тем, как обстоят дела, и тем, как они должны обстоять.", "Конрад Лоренц"),
    ("Оптимист думает, что мы живём в лучшем из миров. Пессимист боится, что так оно и есть.", "Джеймс Кейбел"),
    ("Жизнь слишком коротка, чтобы быть маленьким.", "Бенджамин Дизраэли"),
    ("Не откладывай на завтра то, что можно сделать сегодня.", "Бенджамин Франклин"),
    ("Спящему лисёнку курица не попадается.", None),
    ("Дорогу осилит идущий.", None),
    ("Лучше зажечь свечу, чем проклинать темноту.", "Конфуций"),
    ("Победитель не тот, кто никогда не падает, а тот, кто всегда поднимается.", "Вивиан Ломбарди"),
    ("Сначала они тебя не замечают, потом смеются над тобой, затем борются с тобой. А потом ты побеждаешь.", "Махатма Ганди"),
    ("Будь изменением, которое ты хочешь увидеть в мире.", "Махатма Ганди"),
    ("Слабый никогда не сможет простить. Прощение - это атрибут сильного.", "Махатма Ганди"),
    ("Сила не в теле, а в душе.", "Лев Толстой"),
    ("Все великие дела сначала кажутся невозможными.", "Томас Карлейль"),
    ("Верить - значит знать то, чего ты не видишь. Награда за эту веру - видеть то, во что веришь.", "Аврелий Августин"),
    ("Человек, который никогда не ошибался, никогда не пробовал ничего нового.", "Альберт Эйнштейн"),
    ("Завтра нужно начинать жить правильно. Сегодня нет времени.", None),
    ("Настоящая мудрость приходит к нам, когда мы осознаём, как мало мы знаем.", "Сократ"),
    ("Я знаю, что я ничего не знаю.", "Сократ"),
    ("Относись к другим так, как хочешь, чтобы относились к тебе.", "Библия"),
    ("Жизнь подобна ехать на велосипеде. Чтобы сохранить равновесие, нужно двигаться.", "Альберт Эйнштейн"),
    ("Каждый ребёнок - художник. Проблема в том, как остаться художником, повзрослев.", "Пабло Пикассо"),
    ("Вдохновение существует, но оно должно застать тебя за работой.", "Пабло Пикассо"),
    ("Смысл жизни в том, чтобы найти свой дар. Цель жизни - отдать его.", "Пабло Пикассо"),
    ("Только те, кто осмеливаются на великие провалы, могут добиться великих успехов.", "Роберт Кеннеди"),
    ("Если долго мучиться - что-нибудь получится.", None),
    ("Ничто так не обманывает, как чрезмерная осторожность.", "Чарльз Диккенс"),
    ("Скажи мне, кто твой друг, и я скажу тебе, кто ты.", None),
    ("Лучше поздно, чем никогда.", None),
    ("Всё гениальное - просто.", None),
    ("Клевета - это трусость человека, который боится говорить правду в лицо.", "Мольер"),
    ("Ум - это то, что вы думаете. Характер - это то, что вы делаете.", None),
    ("Прежде чем говорить - думай. Прежде чем обещать - взвешивай. Прежде чем действовать - жди.", None),
    ("Надежда - это сон наяву.", "Аристотель"),
    ("Человек по природе своей есть существо политическое.", "Аристотель"),
    ("Платон мне друг, но истина дороже.", "Аристотель"),
    ("Цель оправдывает средства.", "Никколо Макиавелли"),
    ("Кто владеет информацией - тот владеет миром.", "Натан Ротшильд"),
    ("Деньги не пахнут.", "Веспасиан"),
    ("Все дороги ведут в Рим.", None),
    ("Сделал дело - гуляй смело.", None),
    ("Не место красит человека, а человек место.", None),
    ("Один в поле не воин.", None),
    ("Терпение и труд всё перетрут.", None),
    ("С кем поведёшься, от того и наберёшься.", None),
    ("В тихом омуте черти водятся.", None),
    ("Кто ищет, тот всегда найдёт.", "Аркадий Гайдар"),
    ("Нет пророка в своём отечестве.", "Библия"),
    ("Хочешь мира - готовься к войне.", "Вегеций"),
    ("Divide et impera - разделяй и властвуй.", "Юлий Цезарь"),
    ("Пришёл, увидел, победил.", "Юлий Цезарь"),
    ("Жребий брошен.", "Юлий Цезарь"),
    ("Я пришёл не разрушить закон, но исполнить.", "Библия"),
    ("Кто предупреждён - тот вооружён.", None),
    ("В споре рождается истина.", "Сократ"),
    ("Лучше молчать и казаться дураком, чем открыть рот и развеять все сомнения.", "Авраам Линкольн"),
    ("Никогда не прерывайте врага, когда он делает ошибку.", "Наполеон Бонапарт"),
    ("Невозможное - это просто слово из словаря дураков.", "Наполеон Бонапарт"),
    ("Армия баранов под командованием льва лучше, чем армия львов под командованием барана.", "Наполеон Бонапарт"),
    ("Говорят, что время всё лечит. Не верьте. Время только учит скрывать боль.", None),
    ("Никогда не позволяйте кому-то быть вашим приоритетом, оставаясь лишь его опцией.", "Марк Твен"),
    ("Правда - это то, что вы скажете через двадцать лет.", "Марк Твен"),
    ("Слухи о моей смерти сильно преувеличены.", "Марк Твен"),
    ("Классик - это книга, которую все хвалят и никто не читает.", "Марк Твен"),
    ("Быть хорошим - недостаточно; человек должен хотеть быть хорошим.", "Станислав Ежи Лец"),
    ("Когда тебе дали лимон - сделай лимонад.", "Дейл Карнеги"),
    ("Улыбайтесь - это всех раздражает.", "Станислав Ежи Лец"),
    ("Мечта - это то, что не даёт вам спать.", None),
    ("Победа - это не финал, поражение - не фатально. Важно мужество продолжать.", "Уинстон Черчилль"),
    ("Никогда не сдавайтесь!", "Уинстон Черчилль"),
    ("Успех - это способность идти от неудачи к неудаче, не теряя энтузиазма.", "Уинстон Черчилль"),
]


# ---------------------------------------------------------------------------
# Главная точка входа
# ---------------------------------------------------------------------------

TARGET = 20_000


async def run_fetch(target: int = TARGET):
    await init_db()

    # Шаг 1: всегда подгружаем свежий шаблон ЗЛВ (1 запрос, ~11 фактов).
    # INSERT OR IGNORE — дубликаты молча отбрасываются.
    # Это гарантирует пополнение свежими фактами при каждом рестарте.
    log.info("Подгружаем свежий шаблон 'Знаете ли вы'...")
    async with httpx.AsyncClient(headers=HEADERS) as client:
        fresh = await fetch_did_you_know_current(client)
    if fresh:
        await insert_quotes([(t, None) for t in fresh])
        log.info(f"Свежий ЗЛВ: получено {len(fresh)} фактов (новые добавлены, дубли пропущены)")

    # Шаг 2: если цель уже достигнута — выходим
    current = await count_quotes()
    if current >= target:
        log.info(f"База содержит {current} записей, цель {target} достигнута.")
        return

    log.info(f"Досгружаем до {target}. Сейчас в базе: {current}")

    # Встроенная база (идемпотентно — дубликаты игнорируются)
    await insert_quotes(BUILTIN_QUOTES)
    current = await count_quotes()
    log.info(f"После встроенной базы: {current} записей")

    # WikiQuote
    if current < target:
        wiki_quotes = await fetch_wikiquote(target=target - current + 500)
        if wiki_quotes:
            await insert_quotes(wiki_quotes)
            current = await count_quotes()
            log.info(f"После WikiQuote: {current} записей")

    # Wikipedia "Знаете ли вы" — архив
    if current < target:
        zlv_facts = await fetch_wikipedia_did_you_know(target=min(5000, target - current + 500))
        if zlv_facts:
            await insert_quotes(zlv_facts)
            current = await count_quotes()
            log.info(f"После Wikipedia ЗЛВ: {current} записей")

    # aphorism.ru
    if current < target:
        aph_quotes = await fetch_aphorism_ru()
        if aph_quotes:
            await insert_quotes(aph_quotes)
            current = await count_quotes()
            log.info(f"После aphorism.ru: {current} записей")

    # citaty.info
    if current < target:
        cit_quotes = await fetch_citaty_info()
        if cit_quotes:
            await insert_quotes(cit_quotes)
            current = await count_quotes()
            log.info(f"После citaty.info: {current} записей")

    final = await count_quotes()
    log.info(f"Загрузка завершена. Итого в базе: {final} записей.")


if __name__ == "__main__":
    asyncio.run(run_fetch())
