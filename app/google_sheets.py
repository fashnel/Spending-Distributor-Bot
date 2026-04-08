"""
Модуль для работы с Google Sheets через gspread.
Формат таблицы: | Дата | Еда | Еда вне дома | Транспорт |
Каждый день месяца предзаполнен. Данные из выписки вписываются в нужные даты.
"""

import calendar
from collections import defaultdict
from datetime import datetime

import gspread
from gspread import Cell


# Порядок колонок
CATEGORIES = ["Еда", "Еда вне дома", "Транспорт"]
HEADERS = ["Дата"] + CATEGORIES

SPREADSHEET_NAME = "Example spreadsheet"


def get_google_client(service_account_file: str = "client_secret.json") -> gspread.Client:
    """Создаёт и возвращает авторизованный клиент Google Sheets."""
    return gspread.service_account(filename=service_account_file)


def _generate_month_dates(year: int, month: int) -> list[str]:
    """Генерирует все даты месяца в формате ДД.ММ.ГГГГ."""
    _, days_in_month = calendar.monthrange(year, month)
    return [
        datetime(year, month, d).strftime("%d.%m.%Y")
        for d in range(1, days_in_month + 1)
    ]


def get_or_create_worksheet(
    gc: gspread.Client,
    spreadsheet_name: str = SPREADSHEET_NAME,
) -> tuple[gspread.Worksheet, list[str]]:
    """
    Получает лист с названием текущего месяца.
    Если нет — создаёт, записывает заголовки И все даты месяца.
    Возвращает (worksheet, список_дат).
    """
    now = datetime.now()
    sheet_name = f"{calendar.month_name[now.month]} {now.year}"

    sh = gc.open(spreadsheet_name)

    try:
        worksheet = sh.worksheet(sheet_name)
        # Если лист существует — даты уже есть, возвращаем пустой список
        return worksheet, []
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=sheet_name, rows=40, cols=len(HEADERS))

        # Заголовки
        dates = _generate_month_dates(now.year, now.month)

        # Формируем все строки: [дата, "", "", ""]
        rows = [[d, "", "", ""] for d in dates]

        # Записываем заголовок + даты одной операцией
        worksheet.append_rows([HEADERS] + rows)

        return worksheet, dates


def append_expenses(
    worksheet: gspread.Worksheet,
    expenses: list[tuple[str, str, float]],
    dates: list[str],
) -> int:
    """
    Записывает расходы в «широком» формате.

    expenses — список кортежей: (дата, категория, сумма).
    dates — список всех дат месяца (из get_or_create_worksheet).
    Данные вписываются в строки с соответствующими датами.
    В конце добавляется строка ИТОГО.

    Возвращает количество обновлённых строк.
    """
    if not expenses:
        return 0

    # Маппинг категории → индекс колонки (0-based относительно CATEGORIES)
    cat_index = {cat: i + 1 for i, cat in enumerate(CATEGORIES)}

    # Собираем данные: {дата: {категория: сумма}}
    by_date: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    updated_dates = set()

    for date, category, amount in expenses:
        by_date[date][category] += amount
        updated_dates.add(date)

    # Обновляем строки с датами, где есть данные
    updated_count = 0
    for date in dates:
        if date not in by_date:
            continue

        row_idx = dates.index(date) + 2  # +2: 1-я строка заголовки, индексы с 1
        row_values = [""] * len(HEADERS)
        row_values[0] = date

        for cat, total in by_date[date].items():
            if cat in cat_index:
                row_values[cat_index[cat]] = f"{round(total, 2):,.2f}".replace(",", " ")

        # Обновляем только ячейки с данными (колонки A-D)
        cell_range = [
            Cell(row_idx, col + 1, val)
            for col, val in enumerate(row_values)
        ]
        worksheet.update_cells(cell_range)
        updated_count += 1

    # Добавляем строку ИТОГО (после всех дат)
    totals = defaultdict(float)
    for _, category, amount in expenses:
        totals[category] += amount

    total_row = ["ИТОГО"]
    for cat in CATEGORIES:
        total_val = round(totals.get(cat, 0), 2)
        total_row.append(f"{total_val:,.2f}".replace(",", " ") if total_val > 0 else "")

    # Находим первую пустую строку после последней даты
    last_row = len(dates) + 2
    worksheet.append_row(total_row, value_input_option="USER_ENTERED")

    return updated_count
