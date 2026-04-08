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


def _format_number(value: float) -> str:
    """Форматирует число с запятой как десятичный разделитель (например, 24,00)."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


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
        num_categories = len(CATEGORIES)
        num_rows = 40  # с запасом
        worksheet = sh.add_worksheet(
            title=sheet_name,
            rows=num_rows,
            cols=num_categories + 1,
        )

        dates = _generate_month_dates(now.year, now.month)

        # Формируем все строки: [дата, "", "", ""]
        rows = [[d] + [""] * num_categories for d in dates]

        # Записываем заголовок + даты одной операцией
        worksheet.append_rows([HEADERS] + rows, value_input_option="USER_ENTERED")

        # Настраиваем формат чисел для колонок с категориями (B, C, D, ...)
        # B2:D{last_date_row}
        last_date_row = len(dates) + 1  # +1 для строки заголовков
        for col_idx in range(2, num_categories + 2):  # колонки B, C, D... (1-based)
            range_name = f"{_col_letter(col_idx)}2:{_col_letter(col_idx)}{last_date_row}"
            worksheet.format(
                range_name,
                {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}},
            )

        return worksheet, dates


def _col_letter(col_index: int) -> str:
    """Преобразует 1-based индекс колонки в букву (1 -> A, 2 -> B, 27 -> AA)."""
    result = ""
    while col_index > 0:
        col_index, remainder = divmod(col_index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _insert_totals_row(worksheet: gspread.Worksheet, num_dates: int, num_categories: int) -> None:
    """
    Вставляет строку 'Итого:' сразу после диапазона дат с формулами СУММ.

    Args:
        worksheet: объект листа gspread.
        num_dates: количество строк с датами.
        num_categories: количество колонок с категориями расходов.
    """
    total_row = num_dates + 2  # +2: 1 строка заголовков + смещение (индексы с 1)

    # Колонка A: "Итого:"
    worksheet.update_cell(total_row, 1, "Итого:")

    # Колонки B, C, D...: формулы =СУММ(START:END)
    for col_idx in range(2, num_categories + 2):  # 1-based индекс колонки
        col_letter = _col_letter(col_idx)
        start_cell = f"{col_letter}2"
        end_cell = f"{col_letter}{total_row - 1}"
        formula = f"=СУММ({start_cell}:{end_cell})"
        worksheet.update_cell(total_row, col_idx, formula)


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
    В конце добавляется строка ИТОГО с формулами СУММ.

    Возвращает количество обновлённых строк.
    """
    if not expenses:
        # Даже если расходов нет, вставляем строку Итого
        _insert_totals_row(worksheet, len(dates), len(CATEGORIES))
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
                row_values[cat_index[cat]] = _format_number(total)

        # Обновляем только ячейки с данными (колонки A-D)
        cell_range = [
            Cell(row_idx, col + 1, val)
            for col, val in enumerate(row_values)
        ]
        worksheet.update_cells(cell_range, value_input_option="USER_ENTERED")
        updated_count += 1

    # Вставляем строку ИТОГО с формулами СУММ
    _insert_totals_row(worksheet, len(dates), len(CATEGORIES))

    return updated_count
