import os, sys, threading, time, random, traceback
import chess, chess.engine, berserk, requests
from fastapi import FastAPI, HTTPException

TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)
app = FastAPI()
running = True

engine = None
engine_lock = threading.Lock()
active_games = set()          # храним ID активных игр, чтобы не запускать дважды
games_lock = threading.Lock()

@app.get("/health")
def health():
    return {"status": "ok"}

def send_greeting(game_id, opponent):
    msg = random.choice([f"Привет, {opponent}! 🤝", f"Здравствуй, {opponent}. Да победит сильнейший! 🧠"])
    try:
        client.bots.post_message(game_id, msg, spectator=False)
    except:
        pass

def send_game_result(game_id, board, my_id):
    if board.is_checkmate():
        msg = "🏆 Мат! Отличная игра!" if board.turn != my_id else "😞 Мат... поздравляю!"
    elif board.is_stalemate() or board.is_insufficient_material():
        msg = "🤝 Ничья! Спасибо за партию."
    else:
        msg = "Игра завершена. GG!"
    try:
        client.bots.post_message(game_id, msg, spectator=False)
    except:
        pass

def get_move_time(clock, board=None):
    inc = clock.get('increment', 0)
    my_time = clock.get('white' if board and board.turn == chess.WHITE else 'black', 0)
    if my_time < 1.0:
        return 0.05
    if inc <= 1:          # пуля
        return 0.2 if my_time < 3.0 else 0.4
    elif inc <= 3:        # блиц
        return 0.5 if my_time < 5.0 else 1.0
    else:                 # рапид/классика
        return 1.0 if my_time < 10.0 else 3.0

def init_engine():
    global engine
    print("Загружаем Stockfish 18...")
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    engine.configure({
        "Skill Level": 20,
        "Hash": 64,
        "Threads": 1,
        "Move Overhead": 50,
    })
    print("Stockfish загружен.")

def get_best_move(board, move_time):
    try:
        result = engine.play(board, chess.engine.Limit(time=move_time))
        return result.move.uci() if result.move else None
    except Exception as e:
        print(f"Ошибка хода: {e}")
        return None

def make_move_with_retry(game_id, board, move_time):
    for attempt in range(2):
        try:
            move_uci = get_best_move(board, move_time)
            if not move_uci:
                return False
            client.bots.make_move(game_id, move_uci)
            print(f"[{game_id}] >>> {move_uci} ({move_time:.3f}s)")
            sys.stdout.flush()
            return True
        except Exception as e:
            print(f"[{game_id}] Попытка {attempt+1} не удалась: {e}")
            time.sleep(0.1)
    return False

def play_game(game_id, initial_fen):
    # Проверяем, не запущена ли уже эта игра
    with games_lock:
        if game_id in active_games:
            print(f"[{game_id}] Игра уже запущена, пропускаем")
            return
        active_games.add(game_id)
    try:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        my_id = client.account.get()['id']
        white_id = black_id = None
        while True:
            try:
                stream = client.bots.stream_game_state(game_id)
                for event in stream:
                    if 'clock' in event:
                        move_time = get_move_time(event['clock'], board)
                    if event['type'] == 'gameFull':
                        white_id = event.get('white', {}).get('id')
                        black_id = event.get('black', {}).get('id')
                        opponent = black_id if white_id == my_id else white_id
                        send_greeting(game_id, opponent)
                        moves = event.get('state', {}).get('moves', '')
                        if moves:
                            for m in moves.split():
                                board.push_uci(m)
                    elif event['type'] == 'gameState':
                        moves = event.get('moves', '')
                        if moves:
                            cur = moves.split()
                            while len(cur) > len(board.move_stack):
                                board.push_uci(cur[len(board.move_stack)])
                        if white_id is None:
                            white_id = event.get('white', {}).get('id')
                        if black_id is None:
                            black_id = event.get('black', {}).get('id')
                    else:
                        continue
                    if event.get('status') and event.get('status') != 'started':
                        send_game_result(game_id, board, my_id)
                        return
                    if white_id is None or black_id is None:
                        continue
                    if board.turn == chess.WHITE and white_id == my_id:
                        make_move_with_retry(game_id, board, move_time)
                    elif board.turn == chess.BLACK and black_id == my_id:
                        make_move_with_retry(game_id, board, move_time)
            except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
                print(f"[{game_id}] Ошибка соединения: {e}. Переподключение...")
                time.sleep(5)
                continue
            except Exception as e:
                print(f"[{game_id}] Критическая ошибка: {e}")
                traceback.print_exc()
                break
    except Exception as e:
        print(f"[{game_id}] Внешняя ошибка: {e}")
    finally:
        with games_lock:
            active_games.discard(game_id)

@app.get("/challenge/{username}")
def manual_challenge(username: str):
    try:
        client.challenges.create(
            username=username,
            rated=True,
            clock_limit=300,
            clock_increment=3,
            color="random",
            variant="standard"
        )
        return {"status": "ok", "message": f"Challenge sent to {username}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run_bot():
    init_engine()
    print("Бот запущен. Ожидание вызовов...")
    my_id = client.account.get()['id']
    print(f"Мой ID: {my_id}")
    while running:
        try:
            for event in client.bots.stream_incoming_events():
                print(f"Входящее событие: {event.get('type')}")
                if event['type'] == 'challenge':
                    ch = event['challenge']
                    if ch['challenger']['id'] == my_id:
                        # Это наш собственный вызов, не принимаем его, но и не пропускаем — игра начнётся по gameStart
                        print(f"Собственный вызов {ch['id']}, ожидаем gameStart")
                        continue
                    print(f"Получен вызов от {ch['challenger']['id']}")
                    client.bots.accept_challenge(ch['id'])
                    print(f"Вызов принят, ID игры: {ch['id']}")
                    threading.Thread(target=play_game, args=(ch['id'], ch.get('initialFen')), daemon=True).start()
                elif event['type'] == 'gameStart':
                    game = event['game']
                    game_id = game['id']
                    initial_fen = game.get('initialFen')
                    print(f"Начало игры {game_id} (наш вызов был принят)")
                    threading.Thread(target=play_game, args=(game_id, initial_fen), daemon=True).start()
        except Exception as e:
            print(f"Ошибка в главном цикле: {e}. Пауза 30 сек.")
            time.sleep(30)

threading.Thread(target=run_bot, daemon=True).start()
