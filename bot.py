import os, sys, threading, time, random, traceback
import chess, berserk, requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from collections import Counter
from datetime import datetime, timedelta

TOKEN = os.environ.get("LICHESS_TOKEN")
ENGINE_URLS = [
    "https://stboch.onrender.com",
    "https://brasche.onrender.com",
    "https://cloche.onrender.com",
]

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)
app = FastAPI()
running = True
active_games = set()
games_lock = threading.Lock()
MAX_CONCURRENT_GAMES = 3

# Хранилище активных издевательств
bully_data = {}
bully_lock = threading.Lock()
bully_worker_running = False
BULLY_INTERVAL = 5  # секунд между вызовами

# Чтение HTML
HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")
try:
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        HTML_CONTENT = f.read()
except:
    HTML_CONTENT = "<html><body><h1>SkyBotinok</h1></body></html>"

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_CONTENT

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/challenge/{username}")
def manual_challenge(
    username: str,
    clock_limit: int = 5,
    clock_increment: int = 3,
    color: str = "random",
    rated: bool = True
):
    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    if color not in ["random", "white", "black"]:
        raise HTTPException(status_code=400, detail="Invalid color")
    try:
        client.challenges.create(
            username=username,
            rated=rated,
            clock_limit=clock_limit * 60,
            clock_increment=clock_increment,
            color=color,
            variant="standard"
        )
        return {"status": "ok", "message": f"Challenge sent to {username} ({clock_limit}+{clock_increment}, {color})"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/start_bully")
def start_bully_route(data: dict):
    global bully_worker_running
    username = data.get("username")
    if not username:
        raise HTTPException(400, "Username required")
    clock_limit = int(data.get("clock_limit", 5))
    clock_increment = int(data.get("clock_increment", 3))
    color = data.get("color", "random")
    rated = data.get("rated", True)
    limit_type = data.get("limit_type", "infinite")
    end_time_str = data.get("end_time")
    games_count = data.get("games_count")

    end_datetime = None
    games_left = None

    if limit_type == "time" and end_time_str:
        try:
            h, m = map(int, end_time_str.split(':'))
            now = datetime.now()
            end_datetime = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if end_datetime < now:
                end_datetime += timedelta(days=1)
        except:
            raise HTTPException(400, "Invalid time format")
    elif limit_type == "games" and games_count:
        games_left = int(games_count)
    else:
        games_left = -1  # бесконечно

    with bully_lock:
        bully_data[username] = {
            'clock_limit': clock_limit,
            'clock_increment': clock_increment,
            'color': color,
            'rated': rated,
            'games_left': games_left,
            'end_datetime': end_datetime,
        }
    # Запускаем фоновый поток, если ещё не запущен
    if not bully_worker_running:
        bully_worker_running = True
        threading.Thread(target=bully_worker, daemon=True).start()
    return {"status": "ok", "message": f"Bullying of {username} started. Interval {BULLY_INTERVAL}s"}

@app.post("/stop_bully")
def stop_bully_route(data: dict):
    username = data.get("username")
    if not username:
        raise HTTPException(400, "Username required")
    with bully_lock:
        if username in bully_data:
            del bully_data[username]
            return {"status": "ok", "message": f"Stopped bullying {username}"}
        else:
            return {"status": "not_found", "message": f"No active bullying for {username}"}

def bully_worker():
    """Фоновый поток: каждые BULLY_INTERVAL секунд отправляет вызовы для всех целей."""
    while bully_worker_running:
        # Копируем список целей, чтобы не блокировать надолго
        with bully_lock:
            targets = list(bully_data.items())
        for target, info in targets:
            # Проверяем лимиты
            now = datetime.now()
            if info['end_datetime'] and now > info['end_datetime']:
                with bully_lock:
                    if target in bully_data:
                        del bully_data[target]
                print(f"[БУЛЛИНГ] Время истекло для {target}")
                continue
            if info['games_left'] is not None:
                if info['games_left'] <= 0:
                    with bully_lock:
                        if target in bully_data:
                            del bully_data[target]
                    print(f"[БУЛЛИНГ] Лимит партий исчерпан для {target}")
                    continue
                else:
                    # Уменьшаем счётчик после отправки
                    with bully_lock:
                        if target in bully_data:
                            bully_data[target]['games_left'] -= 1
                    print(f"[БУЛЛИНГ] Отправка вызова {target}, осталось: {bully_data[target]['games_left'] if bully_data[target]['games_left'] != -1 else '∞'}")
            else:
                print(f"[БУЛЛИНГ] Отправка вызова {target} (бесконечно)")
            # Отправляем вызов
            try:
                client.challenges.create(
                    username=target,
                    rated=info['rated'],
                    clock_limit=info['clock_limit'] * 60,
                    clock_increment=info['clock_increment'],
                    color=info['color'],
                    variant="standard"
                )
            except Exception as e:
                print(f"[БУЛЛИНГ] Ошибка вызова для {target}: {e}")
        time.sleep(BULLY_INTERVAL)

# --- Остальные функции (игра, ходы) без изменений ---
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

def get_move_time(clock, board):
    inc = clock.get('increment', 0)
    my_time = clock.get('white' if board.turn == chess.WHITE else 'black', 0)
    moves_done = board.fullmove_number
    if my_time < 1.0:
        return 0.05
    if inc <= 1:
        if my_time < 5.0:
            return 0.2
        return 0.4 if moves_done < 30 else 0.3
    if inc <= 3:
        if my_time < 10.0:
            return 0.5
        return 1.0 if moves_done < 25 else 0.8
    if my_time < 20.0:
        return 1.0
    return 2.0 if moves_done < 40 else 1.5

def get_best_move(fen, move_time):
    candidates = []
    timeout = move_time + 2.0
    for url in ENGINE_URLS:
        try:
            resp = requests.post(f"{url}/get_move", json={"fen": fen, "move_time": move_time}, timeout=timeout)
            if resp.status_code == 200:
                move = resp.json().get("move")
                if move:
                    candidates.append(move)
        except Exception as e:
            print(f"Ошибка {url}: {e}")
    if candidates:
        most_common = Counter(candidates).most_common(1)[0][0]
        print(f"Голосование: {most_common} (из {len(candidates)})")
        return most_common
    return None

def make_move(game_id, board, move_time):
    move_uci = get_best_move(board.fen(), move_time)
    if not move_uci:
        return False
    try:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            print(f"[{game_id}] Нелегальный ход {move_uci}")
            return False
        client.bots.make_move(game_id, move_uci)
        print(f"[{game_id}] >>> {move_uci} ({move_time:.2f}s)")
        sys.stdout.flush()
        return True
    except Exception as e:
        print(f"[{game_id}] Ошибка отправки хода: {e}")
        return False

def play_game(game_id, initial_fen):
    with games_lock:
        active_games.add(game_id)
    try:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        my_id = client.account.get()['id']
        white_id = black_id = None
        print(f"[{game_id}] Старт. Мой ID: {my_id}")
        while True:
            try:
                stream = client.bots.stream_game_state(game_id)
                for event in stream:
                    if 'clock' in event:
                        move_time = get_move_time(event['clock'], board)
                    if event['type'] == 'gameFull':
                        white_id = event.get('white', {}).get('id')
                        black_id = event.get('black', {}).get('id')
                        moves_str = event.get('state', {}).get('moves', '')
                        if moves_str:
                            moves = moves_str.split()
                            while len(moves) > len(board.move_stack):
                                board.push_uci(moves[len(board.move_stack)])
                        print(f"[{game_id}] gameFull: white={white_id} black={black_id} turn={board.turn} moves={len(board.move_stack)}")
                        opponent = black_id if white_id == my_id else white_id
                        send_greeting(game_id, opponent)
                    elif event['type'] == 'gameState':
                        moves_str = event.get('moves', '')
                        if moves_str:
                            moves = moves_str.split()
                            while len(moves) > len(board.move_stack):
                                board.push_uci(moves[len(board.move_stack)])
                        if white_id is None:
                            white_id = event.get('white', {}).get('id')
                        if black_id is None:
                            black_id = event.get('black', {}).get('id')
                    else:
                        continue
                    if event.get('status') and event.get('status') != 'started':
                        print(f"[{game_id}] Завершена: {event.get('status')}")
                        send_game_result(game_id, board, my_id)
                        return
                    if white_id is None or black_id is None:
                        continue
                    if (board.turn == chess.WHITE and white_id == my_id) or (board.turn == chess.BLACK and black_id == my_id):
                        success = make_move(game_id, board, move_time)
                        if not success:
                            print(f"[{game_id}] Ход не удался, ждём...")
                            time.sleep(0.5)
            except (berserk.exceptions.ApiError, requests.exceptions.ConnectionError) as e:
                print(f"[{game_id}] Ошибка соединения: {e}. Переподключение через 5 сек...")
                time.sleep(5)
                continue
            except Exception as e:
                print(f"[{game_id}] Критическая ошибка: {e}")
                traceback.print_exc()
                break
    except Exception as e:
        print(f"[{game_id}] Внешняя ошибка: {e}")
        traceback.print_exc()
    finally:
        with games_lock:
            active_games.discard(game_id)

def run_bot():
    print("Главный бот запущен. Ожидание вызовов...")
    my_id = client.account.get()['id']
    while running:
        try:
            for event in client.bots.stream_incoming_events():
                if event['type'] == 'challenge':
                    ch = event['challenge']
                    challenger = ch['challenger']['id']
                    if challenger == my_id:
                        continue
                    if len(active_games) >= MAX_CONCURRENT_GAMES:
                        print(f"Отклонён вызов от {challenger}: много игр ({len(active_games)})")
                        continue
                    print(f"Вызов от {challenger} принят")
                    client.bots.accept_challenge(ch['id'])
                    threading.Thread(target=play_game, args=(ch['id'], ch.get('initialFen')), daemon=True).start()
                elif event['type'] == 'gameStart':
                    game = event['game']
                    game_id = game['id']
                    if game_id not in active_games:
                        threading.Thread(target=play_game, args=(game_id, game.get('initialFen')), daemon=True).start()
        except Exception as e:
            print(f"Ошибка в главном цикле: {e}. Пауза 30 сек.")
            time.sleep(30)

threading.Thread(target=run_bot, daemon=True).start()
