import os
import sys
import threading
import traceback
import chess
import chess.engine
import berserk
from fastapi import FastAPI

TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)

app = FastAPI()
engine = None

@app.get("/health")
def health():
    return {"status": "ok"}

def make_move(game_id, board):
    try:
        result = engine.play(board, chess.engine.Limit(time=5.0))
        move = result.move
        if move:
            client.bots.make_move(game_id, move.uci())
            print(f"[{game_id}] Сделан ход {move.uci()}")
    except Exception as e:
        print(f"[{game_id}] Ошибка хода: {e}")
        traceback.print_exc()

def play_game(game_id, initial_fen):
    try:
        stream = client.bots.stream_game_state(game_id)
        # Если initial_fen нет, используем стандартную начальную позицию
        if not initial_fen:
            initial_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        board = chess.Board(initial_fen)
        print(f"[{game_id}] Игра начата. Начальная позиция: {board.fen()}")

        for event in stream:
            if event['type'] == 'gameFull':
                for move in event.get('state', {}).get('moves', '').split():
                    board.push_uci(move)
            elif event['type'] == 'gameState':
                moves = event['moves'].split()
                while len(moves) > len(board.move_stack):
                    board.push_uci(moves[len(board.move_stack)])

            if event.get('status') != 'started':
                print(f"[{game_id}] Игра завершена. Статус: {event.get('status')}")
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

    except Exception as e:
        print(f"[{game_id}] Ошибка в игре: {e}")
        traceback.print_exc()

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

    try:
        for challenge in client.bots.stream_incoming_events():
            if challenge['type'] == 'challenge':
                try:
                    challenger = challenge['challenge']['challenger']['id']
                    print(f"Получен вызов от {challenger}")
                    initial_fen = challenge['challenge'].get('initialFen')
                    client.bots.accept_challenge(challenge['challenge']['id'])
                    print(f"Вызов от {challenger} принят")
                    threading.Thread(
                        target=play_game,
                        args=(challenge['challenge']['id'], initial_fen),
                        daemon=True
                    ).start()
                except Exception as e:
                    print(f"Ошибка при принятии вызова: {e}")
                    traceback.print_exc()
    except Exception as e:
        print(f"Ошибка в главном цикле: {e}")
        traceback.print_exc()

thread = threading.Thread(target=run_bot, daemon=True)
thread.start()
