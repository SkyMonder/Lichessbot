import os
import threading
import chess
import chess.engine
import berserk
from fastapi import FastAPI

TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"   # бинарник, который будет скачан в start.sh

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

# Инициализация клиента Lichess
session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)

app = FastAPI()

engine = None

# ------------------------------------------------------------------
#  Health‑check для cron-job.org (держит бота на Render живым)
# ------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok"}

# ------------------------------------------------------------------
#  Логика игры
# ------------------------------------------------------------------
def make_move(game_id, board):
    """Сделать ход движком Stockfish (3 секунды на ход)."""
    try:
        result = engine.play(board, chess.engine.Limit(time=3.0))
        move = result.move
        if move:
            client.bots.make_move(game_id, move.uci())
            print(f"Сделан ход {move.uci()} в партии {game_id}")
    except Exception as e:
        print(f"Ошибка при ходе: {e}")

def play_game(game_id, initial_fen):
    """Играет одну партию, подписываясь на события."""
    stream = client.bots.stream_game_state(game_id)
    board = chess.Board(initial_fen)

    for event in stream:
        # Обновляем доску новыми ходами
        if event['type'] == 'gameFull':
            for move in event.get('state', {}).get('moves', '').split():
                board.push_uci(move)
        elif event['type'] == 'gameState':
            moves = event['moves'].split()
            while len(moves) > len(board.move_stack):
                board.push_uci(moves[len(board.move_stack)])

        # Определяем, чей ход и наш ли он
        if event.get('status') != 'started':
            break

        try:
            my_id = client.account.get()['id']
        except Exception:
            my_id = None

        if my_id is None:
            continue

        if board.turn == chess.WHITE and event.get('white', {}).get('id') == my_id:
            make_move(game_id, board)
        elif board.turn == chess.BLACK and event.get('black', {}).get('id') == my_id:
            make_move(game_id, board)

def run_bot():
    """Главный цикл: ждёт вызовы и запускает игры."""
    global engine
    try:
        engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    except Exception as e:
        print(f"Не удалось запустить Stockfish: {e}")
        return

    print("Бот запущен. Ожидание вызовов...")
    for challenge in client.bots.stream_incoming_events():
        if challenge['type'] == 'challenge':
            try:
                client.bots.accept_challenge(challenge['challenge']['id'])
                print(f"Принят вызов от {challenge['challenge']['challenger']['id']}")
                # Запускаем игру в отдельном потоке, чтобы не блокировать приём новых вызовов
                threading.Thread(
                    target=play_game,
                    args=(challenge['challenge']['id'], challenge['challenge']['initialFen']),
                    daemon=True
                ).start()
            except Exception as e:
                print(f"Ошибка при принятии вызова: {e}")

# ------------------------------------------------------------------
#  Запускаем бота в фоновом потоке при старте приложения
# ------------------------------------------------------------------
threading.Thread(target=run_bot, daemon=True).start()
