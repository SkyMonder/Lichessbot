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

bully_data = {}
bully_lock = threading.Lock()
last_bully_call = {}  # для защиты от частых вызовов

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
        games_left = -1

    with bully_lock:
        bully_data[username] = {
            'clock_limit': clock_limit,
            'clock_increment': clock_increment,
            'color': color,
            'rated': rated,
            'games_left': games_left,
            'end_datetime': end_datetime,
        }
        print(f"[БУЛЛИНГ] Добавлен {username}, лимит: {games_left if games_left != -1 else '∞'}")

    try:
        client.challenges.create(
            username=username,
            rated=rated,
            clock_limit=clock_limit * 60,
            clock_increment=clock_increment,
            color=color,
            variant="standard"
        )
        return {"status": "ok", "message": f"Bullying of {username} started!"}
    except Exception as e:
        with bully_lock:
            bully_data.pop(username, None)
        raise HTTPException(500, detail=str(e))

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

    # Instant Dominion Mode: если осталось менее 10 секунд
    if my_time < 10.0:
        print(f"[ТАЙМ-МЕНЕДЖМЕНТ] Instant Dominion: {my_time:.1f} сек, глубина 12")
        return -12  # отрицательное значение означает глубину

    # В начале партии (первые 10 ходов) – быстрые ходы
    if moves_done < 10:
        return 0.8

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

def get_best_move(fen, move_time, use_depth=False, depth=12):
    candidates = []
    timeout = (move_time + 2.0) if not use_depth else 1.0
    for url in ENGINE_URLS:
        try:
            if use_depth:
                resp = requests.post(f"{url}/get_move", json={"fen": fen, "depth": depth}, timeout=timeout)
            else:
                resp = requests.post(f"{url}/get_move", json={"fen": fen, "move_time": move_time}, timeout=timeout)
            if resp.status_code == 200:
                move = resp.json().get("move")
                if move:
                    candidates.append(move)
        except Exception as e:
            print(f"Ошибка {url}: {e}")
    if candidates:
        most_common = Counter(candidates).most_common(1)[0][0]
        return most_common
    return None

def make_move(game_id, board, move_time, use_depth=False, depth=12):
    move_uci = get_best_move(board.fen(), move_time, use_depth, depth)
    if not move_uci:
        return False
    try:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            print(f"[{game_id}] Нелегальный ход {move_uci}")
            return False
        client.bots.make_move(game_id, move_uci)
        if use_depth:
            print(f"[{game_id}] >>> {move_uci} (глубина {depth})")
        else:
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
        my_username = client.account.get()['username']
        white_name = black_name = None
        opponent_name = None
        while True:
            try:
                stream = client.bots.stream_game_state(game_id)
                for event in stream:
                    if 'clock' in event:
                        move_time_val = get_move_time(event['clock'], board)
                        use_depth = False
                        depth = 12
                        if isinstance(move_time_val, int) and move_time_val < 0:
                            use_depth = True
                            depth = -move_time_val
                            move_time_sec = 0.05
                        else:
                            move_time_sec = move_time_val
                    if event['type'] == 'gameFull':
                        white_name = event.get('white', {}).get('name')
                        black_name = event.get('black', {}).get('name')
                        moves_str = event.get('state', {}).get('moves', '')
                        if moves_str:
                            moves = moves_str.split()
                            while len(moves) > len(board.move_stack):
                                board.push_uci(moves[len(board.move_stack)])
                        print(f"[{game_id}] gameFull: white={white_name} black={black_name} turn={board.turn}")
                        opponent_name = black_name if white_name == my_username else white_name
                        if opponent_name:
                            send_greeting(game_id, opponent_name)
                    elif event['type'] == 'gameState':
                        moves_str = event.get('moves', '')
                        if moves_str:
                            moves = moves_str.split()
                            while len(moves) > len(board.move_stack):
                                board.push_uci(moves[len(board.move_stack)])
                        if white_name is None:
                            white_name = event.get('white', {}).get('name')
                        if black_name is None:
                            black_name = event.get('black', {}).get('name')
                        if opponent_name is None:
                            opponent_name = black_name if white_name == my_username else white_name
                    else:
                        continue
                    if event.get('status') and event.get('status') != 'started':
                        print(f"[{game_id}] Завершена: {event.get('status')}")
                        send_game_result(game_id, board, my_username)
                        # === БУЛЛИНГ: отправляем следующий вызов ===
                        if opponent_name:
                            with bully_lock:
                                if opponent_name in bully_data:
                                    info = bully_data[opponent_name]
                                    now = datetime.now()
                                    # Проверка лимитов
                                    if info['end_datetime'] and now > info['end_datetime']:
                                        del bully_data[opponent_name]
                                        print(f"[БУЛЛИНГ] Время истекло для {opponent_name}")
                                    elif info['games_left'] is not None:
                                        if info['games_left'] <= 0:
                                            del bully_data[opponent_name]
                                            print(f"[БУЛЛИНГ] Лимит партий для {opponent_name} исчерпан")
                                        else:
                                            if info['games_left'] > 0:
                                                info['games_left'] -= 1
                                            # Защита от частых вызовов
                                            last_time = last_bully_call.get(opponent_name, 0)
                                            if time.time() - last_time > 5:
                                                last_bully_call[opponent_name] = time.time()
                                                try:
                                                    client.challenges.create(
                                                        username=opponent_name,
                                                        rated=info['rated'],
                                                        clock_limit=info['clock_limit'] * 60,
                                                        clock_increment=info['clock_increment'],
                                                        color=info['color'],
                                                        variant="standard"
                                                    )
                                                    print(f"[БУЛЛИНГ] Новый вызов {opponent_name}, осталось: {info['games_left'] if info['games_left'] != -1 else '∞'}")
                                                except Exception as e:
                                                    print(f"[БУЛЛИНГ] Ошибка вызова: {e}")
                                            else:
                                                print(f"[БУЛЛИНГ] Слишком частый вызов {opponent_name}, пропуск")
                                    else:
                                        # Бесконечно
                                        last_time = last_bully_call.get(opponent_name, 0)
                                        if time.time() - last_time > 5:
                                            last_bully_call[opponent_name] = time.time()
                                            try:
                                                client.challenges.create(
                                                    username=opponent_name,
                                                    rated=info['rated'],
                                                    clock_limit=info['clock_limit'] * 60,
                                                    clock_increment=info['clock_increment'],
                                                    color=info['color'],
                                                    variant="standard"
                                                )
                                                print(f"[БУЛЛИНГ] Новый вызов {opponent_name} (бесконечно)")
                                            except Exception as e:
                                                print(f"[БУЛЛИНГ] Ошибка: {e}")
                                        else:
                                            print(f"[БУЛЛИНГ] Слишком частый вызов {opponent_name}, пропуск")
                        else:
                            print(f"[БУЛЛИНГ] opponent_name не определён, буллинг не отправлен")
                        return
                    if white_name is None or black_name is None:
                        continue
                    if (board.turn == chess.WHITE and white_name == my_username) or (board.turn == chess.BLACK and black_name == my_username):
                        if use_depth:
                            success = make_move(game_id, board, 0, use_depth=True, depth=depth)
                        else:
                            success = make_move(game_id, board, move_time_sec)
                        if not success:
                            time.sleep(0.3)
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
    while running:
        try:
            for event in client.bots.stream_incoming_events():
                if event['type'] == 'challenge':
                    ch = event['challenge']
                    if len(active_games) >= MAX_CONCURRENT_GAMES:
                        continue
                    client.bots.accept_challenge(ch['id'])
                    threading.Thread(target=play_game, args=(ch['id'], ch.get('initialFen')), daemon=True).start()
                elif event['type'] == 'gameStart':
                    game = event['game']
                    if game['id'] not in active_games:
                        threading.Thread(target=play_game, args=(game['id'], game.get('initialFen')), daemon=True).start()
        except Exception as e:
            print(f"Ошибка в главном цикле: {e}. Пауза 30 сек.")
            time.sleep(30)

threading.Thread(target=run_bot, daemon=True).start()
