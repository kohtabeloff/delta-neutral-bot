# Деплой бота на VPS

## 1. Загрузи проект на VPS

```bash
# На VPS (например Ubuntu 22.04)
cd ~
git clone <твой-repo> delta-neutral-bot
cd delta-neutral-bot
```

Или через scp если нет git:
```bash
scp -r "Delta-neutral bot/" user@YOUR_VPS_IP:~/delta-neutral-bot/
```

## 2. Установи зависимости

```bash
cd ~/delta-neutral-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Создай .env файл

```bash
cp .env.example .env
nano .env   # заполни все ключи
```

## 4. Проверь что бот запускается

```bash
source venv/bin/activate
python main.py
# Ctrl+C после проверки
```

## 5. Установи systemd сервис

```bash
# Отредактируй файл — замени YOUR_USER на своего пользователя
nano deploy/delta-bot.service

# Скопируй в systemd
sudo cp deploy/delta-bot.service /etc/systemd/system/

# Активируй и запусти
sudo systemctl daemon-reload
sudo systemctl enable delta-bot    # автозапуск при перезагрузке VPS
sudo systemctl start delta-bot
```

## 6. Полезные команды

```bash
# Статус бота
sudo systemctl status delta-bot

# Смотреть логи в реальном времени
journalctl -u delta-bot -f

# Логи за последний час
journalctl -u delta-bot --since "1 hour ago"

# Перезапустить бота (например после обновления кода)
sudo systemctl restart delta-bot

# Остановить
sudo systemctl stop delta-bot
```

## 7. Обновление кода

```bash
cd ~/delta-neutral-bot
git pull           # если используешь git
sudo systemctl restart delta-bot
```
