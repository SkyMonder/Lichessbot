import os
import sys
import threading
import time
import random
import traceback
import chess
import chess.engine
import berserk
from fastapi import FastAPI

# ------------------------------------------------------------------
# Настройки
# ------------------------------------------------------------------
TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)

app = FastAPI()
engine = None
running = True

# Параметры вызова
CHALLENGE_TIME = 5          # минут
CHALLENGE_INCREMENT = 3     # секунд
CHALLENGE_RATED = True
CHALLENGE_COLOR = "random"
CHALLENGE_INTERVAL = 20     # секунд между вызовами
TARGET_RATING_MIN = 1000
TARGET_RATING_MAX = 3000

# ------------------------------------------------------------------
# Health‑check для keep‑alive (cron-job.org)
# ------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ------------------------------------------------------------------
# Функции игры
# ------------------------------------------------------------------
def make_move(game_id, board):
    """Сделать ход движком (5 секунд на ход)"""
    try:
        result = engine.play(board, chess.engine.Limit(time=5.0))
        move = result.move
        if move:
            client.bots.make_move(game_id, move.uci())
            print(f"[{game_id}] >>> Ход {move.uci()}")
            sys.stdout.flush()
    except Exception as e:
        print(f"[{game_id}] Ошибка хода: {e}")
        traceback.print_exc()
        sys.stdout.flush()

def play_game(game_id, initial_fen):
    """Обрабатывает одну партию"""
    try:
        stream = client.bots.stream_game_state(game_id)
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        print(f"[{game_id}] Игра начата. Начальная позиция: {board.fen()}")
        sys.stdout.flush()

        my_id = client.account.get()['id']
        print(f"[{game_id}] Мой ID: {my_id}")
        sys.stdout.flush()

        white_id = None
        black_id = None
        made_first_move = False

        for event in stream:
            print(f"[{game_id}] EVENT: {event['type']}")
            sys.stdout.flush()

            if event['type'] == 'gameFull':
                white_id = event.get('white', {}).get('id')
                black_id = event.get('black', {}).get('id')
                moves = event.get('state', {}).get('moves', '')
                if moves:
                    for move in moves.split():
                        board.push_uci(move)
                print(f"[{game_id}] gameFull: white={white_id}, black={black_id}, my={my_id}, turn={board.turn}")
                if not made_first_move:
                    if board.turn == chess.WHITE and white_id == my_id:
                        print(f"[{game_id}] Ход белых (бота) сразу после gameFull")
                        make_move(game_id, board)
                        made_first_move = True
                    elif board.turn == chess.BLACK and black_id == my_id:
                        print(f"[{game_id}] Ход чёрных (бота) сразу после gameFull")
                        make_move(game_id, board)
                        made_first_move = True
            elif event['type'] == 'gameState':
                moves = event.get('moves', '')
                if moves:
                    current_moves = moves.split()
                    while len(current_moves) > len(board.move_stack):
                        board.push_uci(current_moves[len(board.move_stack)])
                if white_id is None:
                    white_id = event.get('white', {}).get('id')
                if black_id is None:
                    black_id = event.get('black', {}).get('id')
            elif event['type'] == 'gameStart':
                continue
            else:
                continue

            if event.get('status') and event.get('status') != 'started':
                print(f"[{game_id}] Игра завершена. Статус: {event.get('status')}")
                break

            if white_id is None or black_id is None:
                continue

            if board.turn == chess.WHITE and white_id == my_id:
                print(f"[{game_id}] Ход белых (бота) по gameState")
                make_move(game_id, board)
            elif board.turn == chess.BLACK and black_id == my_id:
                print(f"[{game_id}] Ход чёрных (бота) по gameState")
                make_move(game_id, board)

    except Exception as e:
        print(f"[{game_id}] Ошибка в игре: {e}")
        traceback.print_exc()
        sys.stdout.flush()

# ------------------------------------------------------------------
# Отправка вызовов
# ------------------------------------------------------------------
def send_challenge(username):
    """Отправляет вызов конкретному пользователю"""
    try:
        print(f"Отправка вызова {username} ({CHALLENGE_TIME}+{CHALLENGE_INCREMENT})")
        client.challenges.create(
            username=username,
            rated=CHALLENGE_RATED,
            clock_limit=int(CHALLENGE_TIME),          # минуты
            clock_increment=int(CHALLENGE_INCREMENT), # секунды
            color=CHALLENGE_COLOR,
            variant="standard"
        )
        print(f"✓ Вызов отправлен {username}")
        sys.stdout.flush()
    except berserk.exceptions.ApiError as e:
        if hasattr(e, 'response') and e.response is not None:
            print(f"✗ Ошибка вызова {username}: {e.response.status_code} {e.response.text}")
        else:
            print(f"✗ Ошибка вызова {username}: {e}")
    except Exception as e:
        print(f"✗ Ошибка вызова {username}: {e}")
        traceback.print_exc()
        sys.stdout.flush()

def challenge_loop():
    """Периодически отправляет вызовы случайным игрокам из лидерборда"""
    while running:
        time.sleep(CHALLENGE_INTERVAL)
        try:
            # Получаем топ‑200 блиц‑игроков
            leaders = client.users.get_leaderboard(perf_type="blitz", count=200)
            candidates = []
            for entry in leaders:
                rating = entry['perfs']['blitz'].get('rating')
                if rating and TARGET_RATING_MIN <= rating <= TARGET_RATING_MAX:
                    candidates.append(entry['username'])
            if candidates:
                target = random.choice(candidates)
                send_challenge(target)
            else:
                print("Нет подходящих игроков в лидерборде")
        except Exception as e:
            print(f"Ошибка в challenge_loop: {e}")
            traceback.print_exc()
            sys.stdout.flush()

# ------------------------------------------------------------------
# Основной поток бота
# ------------------------------------------------------------------
def run_bot():
    global engine
    try:
        print("Загружаем Stockfish...")
        sys.stdout.flush()
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        engine.configure({"Skill Level": 20})
        print("Stockfish загружен и настроен на максимальную силу.")
        sys.stdout.flush()
    except Exception as e:
        print(f"Не удалось запустить Stockfish: {e}")
        traceback.print_exc()
        return

    print("Бот запущен. Ожидание вызовов...")
    sys.stdout.flush()

    # Запускаем поток рассылки вызовов
    challenger_thread = threading.Thread(target=challenge_loop, daemon=True)
    challenger_thread.start()

    try:
        for challenge in client.bots.stream_incoming_events():
            print(f"Входящее событие: {challenge['type']}")
            sys.stdout.flush()
            if challenge['type'] == 'challenge':
                try:
                    ch = challenge['challenge']
                    challenger = ch['challenger']['id']
                    print(f"Получен вызов от {challenger}")
                    initial_fen = ch.get('initialFen')
                    client.bots.accept_challenge(ch['id'])
                    print(f"Вызов от {challenger} принят")
                    threading.Thread(
                        target=play_game,
                        args=(ch['id'], initial_fen),
                        daemon=True
                    ).start()
                except Exception as e:
                    print(f"Ошибка при принятии вызова: {e}")
                    traceback.print_exc()
    except Exception as e:
        print(f"Ошибка в главном цикле: {e}")
        traceback.print_exc()
    finally:
        global running
        running = False

# Запускаем бота в фоновом потоке (чтобы не блокировать FastAPI)
thread = threading.Thread(target=run_bot, daemon=True)
thread.start()
