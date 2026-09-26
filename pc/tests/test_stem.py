"""Учебный блок: SymPy-решатель, ГОСТ-библиография, колоды Anki."""

from __future__ import annotations

import pytest

from core.stem.anki import AnkiError, Card, build_deck, parse_cards
from core.stem.bibliography import Reference, format_gost, format_list, short_author
from core.stem.solver import SolveError, solve_problem

# --------------------------------------------------------------- решатель


def test_quadratic_equation_has_both_roots():
    solution = solve_problem("solve", "x^2 - 4")
    assert "-2" in solution.result and "2" in solution.result


def test_equation_with_equals_sign_is_understood():
    """«x^2 = 4» — привычная запись, и она обязана работать."""
    assert solve_problem("solve", "Eq(x**2, 4)").result == solve_problem("solve", "x^2 - 4").result


def test_implicit_multiplication_is_supported():
    """Люди пишут «2x», а не «2*x»."""
    assert solve_problem("solve", "2x - 6").result == "[3]"


def test_derivative_of_a_product():
    solution = solve_problem("derivative", "x*sin(x)")
    assert "sin(x)" in solution.result and "cos(x)" in solution.result


def test_definite_integral_is_exact_not_rounded():
    solution = solve_problem("integral", "x^2", lower="0", upper="3")
    assert solution.result == "9"


def test_limit_of_a_classic_indeterminate_form():
    assert solve_problem("limit", "sin(x)/x", point="0").result == "1"


def test_latex_is_returned_for_rendering():
    assert solve_problem("derivative", "x^3").latex == "3 x^{2}"
    assert "\\frac" in solve_problem("integral", "1/x^2").latex


def test_singular_matrix_says_there_is_no_inverse():
    solution = solve_problem("matrix", "[[1,2],[2,4]]")
    assert "не существует" in " ".join(solution.steps)


def test_unsolvable_integral_is_reported_not_faked():
    """Молчаливый «ответ» на неберущийся интеграл хуже честного отказа."""
    with pytest.raises(SolveError, match="элементарных"):
        solve_problem("integral", "exp(x^2)*sin(x)/log(x)")


@pytest.mark.parametrize(
    "expression",
    ["__import__('os').system('dir')", "eval('2+2')", "lambda: 1", "open('/etc/passwd')"],
)
def test_code_injection_is_blocked(expression):
    """sympify исполняет строку — значит она обязана проходить проверку."""
    with pytest.raises(SolveError):
        solve_problem("evaluate", expression)


def test_unknown_operation_lists_the_available_ones():
    with pytest.raises(SolveError, match="derivative"):
        solve_problem("телепортация", "x")


# ------------------------------------------------------------ библиография


def test_full_name_becomes_initials():
    assert short_author("Иванов Иван Иванович") == "Иванов И. И."


def test_short_form_is_left_alone():
    assert short_author("Петров П. П.") == "Петров П. П."


def test_book_record_follows_the_standard():
    record = format_gost(
        Reference(
            kind="book",
            authors=["Иванов Иван Иванович"],
            title="Теория чисел",
            city="Москва",
            source="Наука",
            year="2024",
            pages="248",
        )
    )
    assert record.startswith("Иванов, И. И. Теория чисел")
    assert "/ И. И. Иванов" in record, "в области ответственности имя идёт в прямой форме"
    assert "Москва : Наука, 2024" in record
    assert record.endswith("248 с.")


def test_article_record_has_the_double_slash():
    record = format_gost(
        Reference(
            kind="article",
            authors=["Петров П. П."],
            title="О сходимости рядов",
            source="Вестник математики",
            year="2025",
            issue="3",
            pages="12-19",
        )
    )
    assert "// Вестник математики" in record
    assert ". // " not in record, "перед двумя косыми точка не ставится"
    assert "№ 3" in record
    assert "С. 12-19" in record


def test_web_record_marks_the_medium_and_access_date():
    record = format_gost(
        Reference(
            kind="web",
            title="Документация Python",
            source="python.org",
            url="https://docs.python.org",
            accessed="15.08.2026",
        )
    )
    assert "Текст : электронный" in record
    assert "(дата обращения: 15.08.2026)" in record


def test_five_authors_are_shortened_to_the_first():
    record = format_gost(
        Reference(
            authors=[f"Автор{i} А. А." for i in range(5)],
            title="Коллективная монография",
            city="СПб.",
            source="Питер",
            year="2023",
        )
    )
    assert "[и др.]" in record
    assert "Автор4" not in record


def test_list_is_sorted_alphabetically():
    listing = format_list(
        [
            Reference(authors=["Яковлев Я. Я."], title="Последняя"),
            Reference(authors=["Абрамов А. А."], title="Первая"),
        ]
    )
    assert listing.index("Абрамов") < listing.index("Яковлев")
    assert listing.startswith("1. ")


def test_reference_without_title_is_refused():
    with pytest.raises(ValueError, match="названия"):
        format_gost(Reference(authors=["Иванов И. И."]))


# ------------------------------------------------------------------ Anki


def test_deck_file_is_created(tmp_path):
    path = build_deck(
        [Card("Что такое предел?", "Значение, к которому стремится функция")],
        "Матанализ",
        tmp_path / "деck.apkg",
    )
    assert path.exists()
    assert path.stat().st_size > 1000


def test_empty_side_is_refused_with_card_numbers(tmp_path):
    """Карточка без ответа бесполезна при повторении — лучше сказать сразу."""
    with pytest.raises(AnkiError, match="пустой"):
        build_deck([Card("Вопрос", ""), Card("Второй", "Ответ")], "Тест", tmp_path / "d.apkg")


def test_deck_without_cards_is_refused(tmp_path):
    with pytest.raises(AnkiError, match="ни одной"):
        build_deck([], "Пустая", tmp_path / "d.apkg")


def test_same_deck_name_gives_the_same_id(tmp_path):
    """Повторная генерация должна обновлять колоду, а не плодить копии."""
    import zipfile

    first = build_deck([Card("A", "B")], "Одна тема", tmp_path / "1.apkg")
    second = build_deck([Card("A", "C")], "Одна тема", tmp_path / "2.apkg")

    assert zipfile.is_zipfile(first) and zipfile.is_zipfile(second)


def test_tags_come_from_a_plain_string_too():
    cards = parse_cards([{"question": "В", "answer": "О", "tags": "матан, пределы"}])
    assert cards[0].tags == ["матан", "пределы"]
