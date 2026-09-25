import os
import threading
from flask import Flask

# Импортируем вашего бота
from bot import dp, bot
from aiogram import executor

app = Flask(__name__)

@app.route('/')
def health():
    return "Bot is running"

@app.route('/health')
def health_check():
    return "OK", 200

def run_flask():
    # Render передает порт через переменную окружения PORT
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def run_bot():
    # Запуск бота в отдельном потоке
    executor.start_polling(dp, skip_updates=True)

if __name__ == "__main__":
    # Запускаем Flask в фоне
    threading.Thread(target=run_flask, daemon=True).start()
    # Запускаем бота в основном потоке
    run_bot()
