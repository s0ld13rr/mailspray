# mailspray

**Version 0.5.4**

```
______  ___      __________________
___   |/  /_____ ___(_)__  /_  ___/____________________ _____  __
__  /|_/ /_  __ `/_  /__  /_____ ___  __ \_  ___/  __ `/_  / / /
_  /  / / / /_/ /_  / _  / ____/ /__  /_/ /  /   / /_/ /_  /_/ /
/_/  /_/  \__,_/ /_/  /_/  /____/ _  .___//_/    \__,_/ _\__, /
                                  /_/                 /____/
```

CLI для проверки устойчивости почтовых стеков к перебору паролей: батчи, задержки, джиттер, лимиты попыток на пользователя, форматы логинов по протоколу.

> Для **легитимных** пентестов, аудита и упражнений в изолированных стендах.

---

## ✨ Возможности

| | |
|:---|:---|
| **Протоколы** | OWA, EWS, ADFS, IMAP, SMTP, Roundcube, Zimbra |
| **Контроль нагрузки** | Параллельность (`-t`), пауза между батчами (`-e`), джиттер (`-J`) |
| **Политика попыток** | Лимит на пользователя (`-n`), стоп после успеха (`-S`) |
| **Вывод** | Консоль, плюс опционально `-o` (текст) и `-j` (JSON) |
| **Цели** | Внешние и внутренние хосты, свой CA (`-T` timeout и др. см. `--help`) |

---

## 📦 Установка

**pipx** (рекомендуется, команда `mailspray` в PATH):

```bash
git clone https://github.com/s0ld13rr/mailspray.git
cd mailspray
pipx install .
mailspray --help
```

Без клонирования:

```bash
pipx install git+https://github.com/s0ld13rr/mailspray.git
```

После публикации в PyPI: `pipx install mailspray`.

**Режим разработки:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
mailspray -V
```

Требуется **Python 3.9+**.

---

## 🚀 Быстрый старт

```bash
# Справка по всем флагам
mailspray --help

# Один логин, один протокол (пример: EWS)
mailspray ews https://mail.example.com -k user -p 'password' -d CORP -F upn

# Списки пользователей и паролей, OWA, сглаженная нагрузка
mailspray owa mail.example.com -u users.txt -p passes.txt -d CORP \
  -n 3 -S -e 30 -J 0.3 -t 5
```

---

## 🔌 Протоколы

| Протокол | Порт по умолчанию | Назначение |
|----------|-------------------|------------|
| `owa` | 443 | Outlook Web Access |
| `ews` | 443 | Exchange Web Services |
| `adfs` | 443 | AD FS (UPN), WS-Trust |
| `imap` | 993 | IMAP (TLS / STARTTLS) |
| `smtp` | 587 | SMTP STARTTLS |
| `roundcube` | 443 | Roundcube |
| `zimbra` | 443 | Zimbra |

Формат имени по умолчанию зависит от протокола; домен задаётся `-d`, переопределение формата — `-F` (`auto`, `domain_prefix`, `upn`, `plain`). Полный список опций: **`mailspray --help`**.

---

## 🔗 Ссылки

- Репозиторий: [github.com/s0ld13rr/mailspray](https://github.com/s0ld13rr/mailspray)
