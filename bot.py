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
            print(f"[{game_id}] >>> Ход {move.uci()}")
            sys.stdout.flush()
    except Exception as e:
        print(f"[{game_id}] Ошибка хода: {e}")
        traceback.print_exc()
        sys.stdout.flush()

def play_game(game_id, initial_fen):
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
        started = False

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
                started = True
                print(f"[{game_id}] gameFull: white={white_id}, black={black_id}, my={my_id}, turn={board.turn}")
                sys.stdout.flush()
            elif event['type'] == 'gameState':
                moves = event.get('moves', '')
                if moves:
                    current_moves = moves.split()
                    while len(current_moves) > len(board.move_stack):
                        board.push_uci(current_moves[len(board.move_stack)])
                started = True
                # Если white_id/black_id не определены, берём из event (если есть)
                if white_id is None:
                    white_id = event.get('white', {}).get('id')
                if black_id is None:
                    black_id = event.get('black', {}).get('id')
            elif event['type'] == 'gameStart':
                started = True
                continue
            else:
                continue

            if not started:
                continue

            if event.get('status') and event.get('status') != 'started':
                print(f"[{game_id}] Игра завершена. Статус: {event.get('status')}")
                break

            if white_id is None or black_id is None:
                print(f"[{game_id}] ID игроков ещё не известны")
                continue

            # Определяем очередь хода
            print(f"[{game_id}] turn={board.turn}, white={white_id}, black={black_id}, my={my_id}")
            if board.turn == chess.WHITE and white_id == my_id:
                print(f"[{game_id}] Ход белых (бота)")
                make_move(game_id, board)
            elif board.turn == chess.BLACK and black_id == my_id:
                print(f"[{game_id}] Ход чёрных (бота)")
                make_move(game_id, board)
            else:
                print(f"[{game_id}] Ожидание хода соперника (turn={board.turn})")

    except Exception as e:
        print(f"[{game_id}] Ошибка в игре: {e}")
        traceback.print_exc()
        sys.stdout.flush()

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

thread = threading.Thread(target=run_bot, daemon=True)
thread.start()
