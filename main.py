import os
import staypresent

# Запускает ваш bot.py как отдельный процесс
# и открывает порт, который ожидает Koyeb
staypresent.run(
    "bot.py",
    port=int(os.getenv("PORT", 8080)),
    restart_on_crash=True,
)
