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
CHALLENGE_TIME_MIN = 5
CHALLENGE_INCREMENT_SEC = 3
CHALLENGE_RATED = True
CHALLENGE_COLOR = "random"
CHALLENGE_INTERVAL = 180           # 3 минуты между попытками (чтобы не было 429)
TARGET_RATING_MIN = 1000
TARGET_RATING_MAX = 3000

active_games = 0
games_lock = threading.Lock()

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
    global active_games
    with games_lock:
        active_games += 1
        print(f"[{game_id}] Активных игр: {active_games}")
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
    finally:
        with games_lock:
            active_games -= 1
            print(f"[{game_id}] Активных игр: {active_games}")

def send_challenge(username):
    try:
        clock_limit_sec = CHALLENGE_TIME_MIN * 60
        print(f"Отправка вызова {username} ({CHALLENGE_TIME_MIN}+{CHALLENGE_INCREMENT_SEC})")
        client.challenges.create(
            username=username,
            rated=CHALLENGE_RATED,
            clock_limit=clock_limit_sec,
            clock_increment=CHALLENGE_INCREMENT_SEC,
            color=CHALLENGE_COLOR,
            variant="standard"
        )
        print(f"✓ Вызов отправлен {username}")
        sys.stdout.flush()
    except berserk.exceptions.ApiError as e:
        if hasattr(e, 'response') and e.response is not None:
            if e.response.status_code == 429:
                print("⚠️ Слишком много запросов (429). Делаем паузу 120 секунд.")
                time.sleep(120)
            else:
                print(f"✗ Ошибка вызова {username}: {e.response.status_code} {e.response.text}")
        else:
            print(f"✗ Ошибка вызова {username}: {e}")
    except Exception as e:
        print(f"✗ Ошибка вызова {username}: {e}")
        traceback.print_exc()
        sys.stdout.flush()

def challenge_loop():
    while running:
        # Проверяем, не идёт ли игра
        with games_lock:
            if active_games > 0:
                print(f"Идёт игра ({active_games}), пропускаем отправку вызова.")
                time.sleep(60)
                continue
        time.sleep(CHALLENGE_INTERVAL)
        print("challenge_loop: ищу кандидатов...")
        sys.stdout.flush()
        try:
            leaders = client.users.get_leaderboard(perf_type="blitz", count=200)
            candidates = []
            for entry in leaders:
                rating = entry['perfs']['blitz'].get('rating')
                if rating and TARGET_RATING_MIN <= rating <= TARGET_RATING_MAX:
                    candidates.append(entry['username'])
            if candidates:
                target = random.choice(candidates)
                print(f"Выбран {target} из {len(candidates)} кандидатов")
                send_challenge(target)
            else:
                print("Нет подходящих игроков в лидерборде")
        except Exception as e:
            print(f"Ошибка в challenge_loop: {e}")
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

    challenger_thread = threading.Thread(target=challenge_loop, daemon=True)
    challenger_thread.start()
    print("Поток рассылки вызовов запущен")
    sys.stdout.flush()

    my_id = client.account.get()['id']

    try:
        for event in client.bots.stream_incoming_events():
            print(f"Входящее событие: {event['type']}")
            sys.stdout.flush()
            if event['type'] == 'challenge':
                try:
                    ch = event['challenge']
                    challenger = ch['challenger']['id']
                    if challenger == my_id:
                        print(f"Пропускаем собственный вызов {ch['id']} от {challenger}")
                        continue
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
            elif event['type'] == 'gameStart':
                try:
                    game = event['game']
                    game_id = game['id']
                    initial_fen = game.get('initialFen')
                    if not initial_fen:
                        try:
                            game_info = client.games.get_game_by_id(game_id)
                            initial_fen = game_info.get('initialFen')
                        except:
                            initial_fen = None
                    print(f"Начало игры {game_id}, начальная позиция: {initial_fen}")
                    threading.Thread(
                        target=play_game,
                        args=(game_id, initial_fen),
                        daemon=True
                    ).start()
                except Exception as e:
                    print(f"Ошибка при обработке gameStart: {e}")
                    traceback.print_exc()
    except Exception as e:
        print(f"Ошибка в главном цикле: {e}")
        traceback.print_exc()
    finally:
        global running
        running = False

thread = threading.Thread(target=run_bot, daemon=True)
thread.start()
