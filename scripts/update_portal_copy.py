#!/usr/bin/env python3
from pathlib import Path

PORTAL = Path("portal/index.html")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = PORTAL.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "<h1>Единый центр digital-инструментов</h1>",
        "<h1>Level Group digital dashboard</h1>",
        "Hero title",
    )

    text = replace_once(
        text,
        "Аналитика размещений, контроль креативов, мониторинг рынка и проверка качества трафика — в одной понятной системе с общей навигацией.",
        "Аналитика размещений, контроль креативов, мониторинг рынка и проверка качества трафика — в одной системе с общей навигацией.",
        "Hero description",
    )

    text = replace_once(
        text,
        '      <p>Каждый раздел решает отдельную задачу, но использует единый визуальный язык и общую структуру переходов.</p>\n',
        "",
        "Section description",
    )

    principles = '''    <section class="principles" aria-label="Принципы портала">
      <div class="principle">
        <strong>Одна навигация</strong>
        <span>Одинаковые названия разделов и быстрый переход между инструментами.</span>
      </div>
      <div class="principle">
        <strong>Единый дизайн</strong>
        <span>Общие цвета, типографика, карточки, статусы и правила интерфейса.</span>
      </div>
      <div class="principle">
        <strong>Независимые модули</strong>
        <span>Каждый сервис можно развивать отдельно, не ломая остальные разделы.</span>
      </div>
    </section>
'''
    text = replace_once(text, principles, "", "Portal principles")

    PORTAL.write_text(text, encoding="utf-8")
    print("Portal copy updated")


if __name__ == "__main__":
    main()
