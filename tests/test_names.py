"""
Необъявленные имена: опечатка и забытый импорт не должны доживать до запуска.

Проверяемая беда. В paper.py был вызов settings_log.отметить(), а самого
settings_log в списке импортов не было. Файл прекрасно компилировался,
прекрасно импортировался — и падал с NameError только в тот момент, когда
управление доходило до строки 113. То есть уже на GitHub, посреди прохода,
после того как проверки правил отчитались зелёным.

Это целый класс ошибок: ни компиляция, ни импорт модуля их не ловят,
потому что тело функции разбирается только при вызове. Ловит либо запуск
каждой ветки (чего у нас нет), либо разбор дерева — здесь второе.

Как проверяем. Для каждого файла собираем, что вообще связано в модуле
(импорты, def, class, присваивания) и что связано внутри каждой функции
(аргументы, присваивания, циклы, with-as, except-as, генераторы). Потом
идём по всем именам, которые ЧИТАЮТСЯ, и требуем, чтобы каждое нашлось
хоть в одной охватывающей области или среди встроенных.

Проверка нарочно нестрогая по потоку выполнения: имя, связанное в любом
месте области, считается известным. Иначе «определено внутри if» давало бы
ложные тревоги. Опечатки и забытые импорты она ловит всё равно — а это
ровно то, что нас укусило.

Запуск:  python tests/test_names.py
"""

from __future__ import annotations

import ast
import builtins
import os
import sys

БАЗА = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ПРОПУСК = {".venv", "venv", "__pycache__", "cache", "state", "export", ".git"}

ВСТРОЕННЫЕ = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__annotations__", "__debug__",
}


def _связанные(узел: ast.AST) -> set[str]:
    """Всё, что область связывает: присваивания, циклы, with, except,
    def/class, импорты, walrus, генераторы. Без захода во вложенные
    функции — у них своя область."""
    имена: set[str] = set()

    def цели(t: ast.AST) -> None:
        for у in ast.walk(t):
            if isinstance(у, ast.Name):
                имена.add(у.id)

    def обойти(у: ast.AST, корень: bool = False) -> None:
        for дитя in ast.iter_child_nodes(у):
            if isinstance(дитя, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                имена.add(дитя.name)
                continue                      # внутрь не идём: своя область
            if isinstance(дитя, ast.Lambda):
                continue
            if isinstance(дитя, (ast.Import, ast.ImportFrom)):
                for a in дитя.names:
                    имена.add((a.asname or a.name).split(".")[0])
            elif isinstance(дитя, ast.Assign):
                for t in дитя.targets:
                    цели(t)
            elif isinstance(дитя, (ast.AugAssign, ast.AnnAssign)):
                цели(дитя.target)
            elif isinstance(дитя, ast.NamedExpr):
                цели(дитя.target)
            elif isinstance(дитя, (ast.For, ast.AsyncFor)):
                цели(дитя.target)
            elif isinstance(дитя, (ast.With, ast.AsyncWith)):
                for э in дитя.items:
                    if э.optional_vars is not None:
                        цели(э.optional_vars)
            elif isinstance(дитя, ast.ExceptHandler):
                if дитя.name:
                    имена.add(дитя.name)
            elif isinstance(дитя, (ast.Global, ast.Nonlocal)):
                имена.update(дитя.names)
            elif isinstance(дитя, (ast.ListComp, ast.SetComp, ast.DictComp,
                                   ast.GeneratorExp)):
                for г in дитя.generators:
                    цели(г.target)
            обойти(дитя)

    обойти(узел, корень=True)
    return имена


def _аргументы(ф: ast.AST) -> set[str]:
    a = ф.args
    из = {х.arg for х in (a.posonlyargs + a.args + a.kwonlyargs)}
    if a.vararg:
        из.add(a.vararg.arg)
    if a.kwarg:
        из.add(a.kwarg.arg)
    return из


def проверить_файл(путь: str) -> list[str]:
    src = open(путь, encoding="utf-8").read()
    дерево = ast.parse(src, путь)
    беды: list[str] = []
    внешние = ВСТРОЕННЫЕ | _связанные(дерево)

    def читаемые(узел: ast.AST) -> list[ast.Name]:
        """Имена, которые читаются прямо в этой области (не во вложенных)."""
        из: list[ast.Name] = []

        def обойти(у: ast.AST) -> None:
            for дитя in ast.iter_child_nodes(у):
                if isinstance(дитя, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.Lambda, ast.ClassDef)):
                    continue
                if isinstance(дитя, ast.Name) and isinstance(дитя.ctx, ast.Load):
                    из.append(дитя)
                обойти(дитя)
        обойти(узел)
        return из

    def область(узел: ast.AST, видно: set[str]) -> None:
        for имя in читаемые(узел):
            if имя.id not in видно:
                беды.append(f"{os.path.relpath(путь, БАЗА)}:{имя.lineno}: "
                            f"имя «{имя.id}» нигде не определено")
        for дитя in ast.iter_child_nodes(узел):
            if isinstance(дитя, (ast.FunctionDef, ast.AsyncFunctionDef)):
                область(дитя, видно | _аргументы(дитя) | _связанные(дитя))
            elif isinstance(дитя, ast.ClassDef):
                область(дитя, видно | _связанные(дитя))
            elif isinstance(дитя, ast.Lambda):
                область(дитя, видно | _аргументы(дитя))
            else:
                # обычный узел: вложенные функции внутри if/try/for
                область(дитя, видно)

    область(дерево, внешние)
    # одно и то же имя может встретиться по нескольку раз из-за обхода
    # вложенных узлов: в отчёте оно нужно один раз.
    видели, единожды = set(), []
    for б in беды:
        if б not in видели:
            видели.add(б)
            единожды.append(б)
    return единожды


def файлы() -> list[str]:
    из = []
    for корень, папки, имена in os.walk(БАЗА):
        папки[:] = [п for п in папки if п not in ПРОПУСК and not п.startswith(".")]
        для_файлов = [и for и in имена if и.endswith(".py")]
        из += [os.path.join(корень, и) for и in для_файлов]
    return sorted(из)


def главное() -> int:
    print("НЕОБЪЯВЛЕННЫЕ ИМЕНА\n")
    все_беды: list[str] = []
    проверено = 0
    for п in файлы():
        try:
            беды = проверить_файл(п)
        except SyntaxError as e:
            все_беды.append(f"{os.path.relpath(п, БАЗА)}: не разбирается: {e}")
            continue
        проверено += 1
        все_беды += беды

    print(f"  проверено файлов: {проверено}")
    if все_беды:
        print(f"\n  НЕТ  найдено проблем: {len(все_беды)}")
        for б in все_беды:
            print(f"    {б}")
        print(f"\nПРОВАЛЕНО ПРОВЕРОК: {len(все_беды)}")
        return 1
    print("  OK   ни одного нераспознанного имени")
    print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
    return 0


if __name__ == "__main__":
    raise SystemExit(главное())
