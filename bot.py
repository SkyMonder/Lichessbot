import os, sys, threading, time, random, traceback
import chess, berserk, requests
from fastapi import FastAPI, HTTPException

TOKEN = os.environ.get("LICHESS_TOKEN")
ENGINE_URLS = [
    "https://stboch.onrender.com",
    "https://brasche.onrender.com",
    "https://cloche.onrender.com",
]
EMERGENCY_ENGINE = "https://emech.onrender.com"

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)
app = FastAPI()
running = True
active_games = set()
games_lock = threading.Lock()

@app.get("/health")
def health():
    return {"status": "ok"}

def send_greeting(game_id, opponent):
    msg = random.choice([f"Привет, {opponent}! 🤝", f"Да победит сильнейший, {opponent}! 🧠"])
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
    my_time = clock.get('white' if board and board.turn == chess.WHITE else 'black', 0)
    inc = clock.get('increment', 0)
    moves_made = len(board.move_stack) if board else 0
    if my_time < 1.0:
        return 0.05
    if inc <= 1:
        if my_time < 5.0:
            return 0.2
        return 0.5 if moves_made < 10 else 0.3
    elif inc <= 3:
        if my_time < 10.0:
            return 0.5
        if moves_made < 20:
            return 1.0
        return 0.8 if moves_made > 40 else 1.2
    else:
        if my_time < 20.0:
            return 1.0
        if moves_made < 15:
            return 3.0
        return 2.5 if moves_made < 40 else 2.0

def get_best_move_from_engines(fen, move_time):
    candidates = {}
    timeout = move_time + 2.0
    for url in ENGINE_URLS:
        try:
            resp = requests.post(f"{url}/get_move", json={"fen": fen, "move_time": move_time}, timeout=timeout)
            if resp.status_code == 200:
                move = resp.json().get("move")
                if move:
                    candidates[move] = candidates.get(move, 0) + 1
        except Exception as e:
            print(f"Ошибка {url}: {e}")
    if candidates:
        best = max(candidates.items(), key=lambda x: x[1])[0]
        print(f"Голосование: {best} (голосов: {candidates[best]})")
        return best
    try:
        resp = requests.post(f"{EMERGENCY_ENGINE}/get_move", json={"fen": fen, "move_time": min(move_time, 0.5)}, timeout=1.0)
        if resp.status_code == 200:
            move = resp.json().get("move")
            if move:
                return move
    except:
        pass
    return None

def make_move_with_retry(game_id, board, move_time):
    for attempt in range(3):
        try:
            move_uci = get_best_move_from_engines(board.fen(), move_time)
            if not move_uci:
                print(f"[{game_id}] Нет хода, попытка {attempt+1}")
                time.sleep(0.2)
                continue
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                print(f"[{game_id}] Нелегальный ход {move_uci}")
                return False
            client.bots.make_move(game_id, move_uci)
            print(f"[{game_id}] >>> {move_uci} ({move_time:.2f}s)")
            sys.stdout.flush()
            return True
        except Exception as e:
            print(f"[{game_id}] Ошибка: {e}")
            time.sleep(0.2)
    return False

def play_game(game_id, initial_fen):
    with games_lock:
        if game_id in active_games:
            return
        active_games.add(game_id)
    try:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        my_id = client.account.get()['id']
        white_id = black_id = None
        # Для отслеживания количества сделанных ходов
        last_move_count = 0
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
                        moves_str = event.get('state', {}).get('moves', '')
                        if moves_str:
                            moves = moves_str.split()
                            # Применяем только новые ходы
                            for i in range(last_move_count, len(moves)):
                                board.push_uci(moves[i])
                            last_move_count = len(moves)
                    elif event['type'] == 'gameState':
                        moves_str = event.get('moves', '')
                        if moves_str:
                            moves = moves_str.split()
                            for i in range(last_move_count, len(moves)):
                                board.push_uci(moves[i])
                            last_move_count = len(moves)
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
                    if (board.turn == chess.WHITE and white_id == my_id) or (board.turn == chess.BLACK and black_id == my_id):
                        success = make_move_with_retry(game_id, board, move_time)
                        if not success:
                            print(f"[{game_id}] Не удалось сделать ход, ждём...")
                            time.sleep(0.5)
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
        active_games.discard(game_id)

@app.get("/challenge/{username}")
def manual_challenge(username: str):
    try:
        client.challenges.create(username=username, rated=True, clock_limit=300, clock_increment=3, color="random")
        return {"status": "ok", "message": f"Challenge sent to {username}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def run_bot():
    print("Главный бот запущен. Ожидание вызовов...")
    my_id = client.account.get()['id']
    while running:
        try:
            for event in client.bots.stream_incoming_events():
                if event['type'] == 'challenge':
                    ch = event['challenge']
                    if ch['challenger']['id'] == my_id:
                        continue
                    print(f"Вызов от {ch['challenger']['id']} принят")
                    client.bots.accept_challenge(ch['id'])
                    threading.Thread(target=play_game, args=(ch['id'], ch.get('initialFen')), daemon=True).start()
                elif event['type'] == 'gameStart':
                    game = event['game']
                    print(f"Игра {game['id']} началась")
                    threading.Thread(target=play_game, args=(game['id'], game.get('initialFen')), daemon=True).start()
        except Exception as e:
            print(f"Ошибка: {e}. Пауза 30 сек.")
            time.sleep(30)

threading.Thread(target=run_bot, daemon=True).start()
