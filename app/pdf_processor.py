"""
Модуль для парсинга PDF-выписок и агрегации расходов по датам и категориям.
"""

import io
import re
from collections import defaultdict
from datetime import datetime

import pdfplumber


# Соответствие банковских категорий нашим
BANK_TO_CATEGORY = {
    "Супермаркеты": "Еда",
    "Рестораны и кафе": "Еда вне дома",
    "Транспорт": "Транспорт",
}

# Регулярка: дата, время, произвольный текст, сумма (число с запятой и 2 знаками)
# Сумма может содержать пробелы-разделители тысяч (например, "22 630,13")
# Мы берём ПЕРВОЕ совпадение суммы в строке — это всегда трата, а не остаток
AMOUNT_PATTERN = re.compile(r"(\d[\d\s]*,\d{2})")
DATE_PATTERN = re.compile(r"(\d{2}\.\d{2}\.\d{4})")


def _parse_amount(raw: str) -> float | None:
    """
    Извлекает сумму из строки, удаляя пробелы-разделители тысяч.
    Возвращает float или None, если не найдено.
    """
    match = AMOUNT_PATTERN.search(raw)
    if not match:
        return None
    # Убираем пробелы (разделители тысяч) и меняем запятую на точку
    cleaned = match.group(1).replace(" ", "").replace(",", ".")
    return float(cleaned)


def _parse_date(raw: str) -> str | None:
    """
    Ищет дату в формате ДД.ММ.ГГГГ и возвращает её же как строку.
    """
    match = DATE_PATTERN.search(raw)
    if not match:
        return None
    return match.group(1)


def parse_pdf(file_path: str | io.BytesIO) -> list[str]:  # принимает оба типа
    lines: list[str] = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                for line in text.split("\n"):
                    clean = line.strip()
                    if clean:
                        lines.append(clean)
    return lines


def categorize_and_aggregate(raw_lines: list[str]) -> list[tuple[str, str, float]]:
    """
    Проходит по сырым строкам, определяет дату, категорию и сумму.
    Агрегирует (суммирует) траты по ключу (дата, категория).

    Возвращает список кортежей: [(дата, категория, сумма), ...]
    """
    # Ключ = (дата, категория) → сумма
    aggregated: dict[tuple[str, str], float] = defaultdict(float)

    for line in raw_lines:
        # 1. Определяем дату
        date_str = _parse_date(line)
        if not date_str:
            continue  # Пропускаем строки без даты (заголовки, итоги и т.д.)

        # 2. Определяем категорию
        found_category = "Прочее"
        for bank_cat, our_cat in BANK_TO_CATEGORY.items():
            if bank_cat.lower() in line.lower():
                found_category = our_cat
                break

        if found_category == "Прочее":
            continue  # Не наша категория — пропускаем

        # 3. Извлекаем сумму (первое совпадение — это трата)
        amount = _parse_amount(line)
        if amount is None:
            continue

        # 4. Агрегируем
        aggregated[(date_str, found_category)] += amount

    # Преобразуем в список кортежей, округляя до 2 знаков
    result = [
        (date, cat, round(total, 2))
        for (date, cat), total in aggregated.items()
    ]

    # Сортируем по дате для удобства чтения
    result.sort(key=lambda x: datetime.strptime(x[0], "%d.%m.%Y"))

    return result
