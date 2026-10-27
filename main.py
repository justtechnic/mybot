import os
import staypresent

# 1. Простой ответ для проверки, что сервис жив
staypresent.web.json({"status": "running"})

# 2. Самопинг, чтобы Render не усыплял сервис
# ВАЖНО: Замените на ваш будущий URL Render
MY_RENDER_URL = "https://mybot-z0zj.onrender.com" 
staypresent.cron(MY_RENDER_URL, interval=240)

# 3. Запуск вашего bot.py
staypresent.run(
    "bot.py",
    port=int(os.getenv("PORT", 8080)),
    restart_on_crash=True
)