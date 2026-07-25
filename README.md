# Level Digital Hub

Единая точка входа в digital-инструменты Level Group.

## Разделы

- **Главный портал:** `portal/`
- **Performance Dashboard с общей навигацией:** `dashboard/`
- **Исходный дашборд без оболочки:** корневой `index.html`
- **Общие компоненты интерфейса:** `shared/`

Портал объединяет четыре направления:

1. Performance Dashboard
2. Creative Quality Hub
3. Realty Intelligence
4. Traffic Fraud Lab

## Архитектура первого этапа

Текущий рабочий дашборд сохранён без изменений. Папка `dashboard/` открывает его внутри безопасной оболочки с общей навигацией. Компоненты `shared/navigation.css` и `shared/navigation.js` можно подключать к остальным проектам по мере их переноса в общую экосистему.
