import os
import threading
import time
import random
import chess
import chess.engine
import berserk
from fastapi import FastAPI
from contextlib import asynccontextmanager

# ---------- Конфигурация через переменные окружения ----------
TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"
# Параметры вызовов (можно менять в настройках Render)
CHALLENGE_ENABLED = os.environ.get("CHALLENGE_ENABLED", "true").lower() == "true"
CHALLENGE_INTERVAL = int(os.environ.get("CHALLENGE_INTERVAL", "60"))  # секунд между вызовами
TIME_CONTROL = os.environ.get("TIME_CONTROL", "600+0")  # 10 минут без инкремента
RATED = os.environ.get("RATED", "true").lower() == "true"
COLOR = os.environ.get("COLOR", "random")  # random, white, black
VARIANT = os.environ.get("VARIANT", "standard")
# Для ограничения рейтинга соперников (опционально)
MIN_RATING = int(os.environ.get("MIN_RATING", "0"))
MAX_RATING = int(os.environ.get("MAX_RATING", "3000"))
# Сила Stockfish: время на ход в секундах (0.5-10)
THINKING_TIME = float(os.environ.get("THINKING_TIME", "3.0"))

# ---------- Инициализация ----------
if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)

app = FastAPI()

engine = None
challenge_thread = None
stop_challenge = threading.Event()

# ---------- Health check ----------
@app.get("/health")
def health():
    return {"status": "ok"}

# ---------- Функция для отправки вызова ----------
def send_challenge():
    """Создаёт вызов случайному онлайн-пользователю или открытый вызов."""
    # Получаем список активных пользователей (можно через berserk, но там нет прямого метода)
    # Вместо этого создаём открытый вызов (public challenge), который увидят все.
    # Это проще и эффективнее.
    try:
        # Создаём открытый вызов (без конкретного соперника)
        challenge_data = {
            "rated": RATED,
            "clock": {
                "limit": int(TIME_CONTROL.split("+")[0]) * 60,  # минуты в секунды
                "increment": int(TIME_CONTROL.split("+")[1])
            },
            "days": None,  # для соответчиков не используется
            "color": COLOR,
            "variant": {"key": VARIANT},
            "rules": "standard",  # или "noAbort", "noGiveup" и т.д.
        }
        # Отправляем открытый вызов через API ботов
        # Для ботов есть специальный метод create_public_challenge
        # Но он требует, чтобы аккаунт был ботом.
        # Используем client.bots.create_public_challenge
        client.bots.create_public_challenge(
            rated=RATED,
            clock_limit=int(TIME_CONTROL.split("+")[0]) * 60,
            clock_increment=int(TIME_CONTROL.split("+")[1]),
            color=COLOR,
            variant=VARIANT,
            days=1,  # для соответчиков не используется, но параметр обязателен
            rules="standard"
        )
        print(f"Открытый вызов создан: {TIME_CONTROL}, рейтинговый={RATED}, цвет={COLOR}")
    except Exception as e:
        print(f"Ошибка при создании вызова: {e}")

def challenge_worker():
    """Фоновый поток, который периодически кидает вызовы."""
    while not stop_challenge.is_set():
        if CHALLENGE_ENABLED:
            send_challenge()
        # Ждём указанный интервал (или пока не скажут остановиться)
        for _ in range(CHALLENGE_INTERVAL):
            if stop_challenge.is_set():
                break
            time.sleep(1)

# ---------- Логика игры (улучшенная) ----------
def make_move(game_id, board):
    """Сделать ход с заданной силой."""
    try:
        # Используем лимит времени на ход
        result = engine.play(board, chess.engine.Limit(time=THINKING_TIME))
        move = result.move
        if move:
            client.bots.make_move(game_id, move.uci())
            print(f"[{game_id}] Ход {move.uci()}")
    except Exception as e:
        print(f"[{game_id}] Ошибка при ходе: {e}")

def play_game(game_id, initial_fen):
    """Основной игровой цикл."""
    try:
        stream = client.bots.stream_game_state(game_id)
        board = chess.Board(initial_fen)
        my_id = client.account.get()['id']
        print(f"[{game_id}] Начинаем игру. Я: {my_id}")

        for event in stream:
            # Обновление доски
            if event['type'] == 'gameFull':
                # Применяем уже сделанные ходы
                for move in event.get('state', {}).get('moves', '').split():
                    board.push_uci(move)
            elif event['type'] == 'gameState':
                moves = event['moves'].split()
                while len(moves) > len(board.move_stack):
                    board.push_uci(moves[len(board.move_stack)])

            # Если игра закончена, выходим
            if event.get('status') != 'started':
                print(f"[{game_id}] Игра завершена")
                break

            # Если очередь хода за нами
            if board.turn == chess.WHITE and event.get('white', {}).get('id') == my_id:
                make_move(game_id, board)
            elif board.turn == chess.BLACK and event.get('black', {}).get('id') == my_id:
                make_move(game_id, board)

    except Exception as e:
        print(f"[{game_id}] Ошибка в игровом потоке: {e}")

def run_bot():
    """Главный цикл: принимаем вызовы и запускаем игры."""
    global engine
    try:
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        # Опционально: установить уровень (например, "skill level" 20 – максимальный)
        # engine.configure({"Skill Level": 20})  # если нужно, раскомментировать
    except Exception as e:
        print(f"Не удалось запустить Stockfish: {e}")
        return

    print("Бот запущен. Ожидание вызовов...")
    # Бесконечный цикл обработки входящих событий (вызовы, игры)
    for event in client.bots.stream_incoming_events():
        if event['type'] == 'challenge':
            # Принимаем вызов, если он соответствует нашим критериям (можно добавить фильтрацию)
            challenge = event['challenge']
            # Проверка рейтинга (если задан)
            opp_rating = challenge.get('rating', 0)
            if opp_rating < MIN_RATING or opp_rating > MAX_RATING:
                print(f"Отклонён вызов от {challenge['challenger']['id']}: рейтинг {opp_rating} вне диапазона")
                client.bots.decline_challenge(challenge['id'], reason="rating")
                continue

            # Принимаем вызов
            try:
                client.bots.accept_challenge(challenge['id'])
                print(f"Принят вызов от {challenge['challenger']['id']} (рейтинг {opp_rating})")
                # Запускаем игру в отдельном потоке
                threading.Thread(
                    target=play_game,
                    args=(challenge['id'], challenge.get('initialFen', 'startpos')),
                    daemon=True
                ).start()
            except Exception as e:
                print(f"Ошибка при принятии вызова: {e}")

# ---------- Запуск бота и фонового потока рассылки вызовов ----------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Запускаем бота в фоне
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Запускаем поток рассылки вызовов
    global challenge_thread
    if CHALLENGE_ENABLED:
        challenge_thread = threading.Thread(target=challenge_worker, daemon=True)
        challenge_thread.start()
        print(f"Поток рассылки вызовов запущен (интервал {CHALLENGE_INTERVAL} сек)")

    yield  # приложение работает

    # При завершении приложения можно корректно остановить потоки
    if CHALLENGE_ENABLED:
        stop_challenge.set()
        if challenge_thread:
            challenge_thread.join(timeout=5)
    if engine:
        engine.quit()

# Назначаем lifespan для приложения
app = FastAPI(lifespan=lifespan)

# Health endpoint (уже был)
@app.get("/health")
def health():
    return {"status": "ok"}
