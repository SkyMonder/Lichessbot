import os, sys, threading, time, random, traceback
import chess, chess.engine, berserk, requests
from fastapi import FastAPI, HTTPException

TOKEN = os.environ.get("LICHESS_TOKEN")
STOCKFISH_PATH = "./stockfish"
BERSERK_PATH = "./berserk_engine"
CLOVER_PATH = "./clover_engine"

if not TOKEN:
    raise RuntimeError("LICHESS_TOKEN environment variable not set")

session = berserk.TokenSession(TOKEN)
client = berserk.Client(session)
app = FastAPI()
running = True

engines = {}
engine_lock = threading.Lock()
active_games = 0
games_lock = threading.Lock()

@app.get("/health")
def health():
    return {"status": "ok"}

# ---------- Чат ----------
def send_greeting(game_id, opponent):
    msg = random.choice([f"Привет, {opponent}! 🤝", f"Здравствуй, {opponent}. Да победит сильнейший! 🧠"])
    try:
        client.bots.post_message(game_id, msg, spectator=False)
        print(f"[{game_id}] {msg}")
    except Exception as e:
        print(f"[{game_id}] Ошибка чата: {e}")

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

# ---------- Адаптивное время ----------
def get_move_time(clock):
    inc = clock.get('increment', 0)
    return 0.5 if inc <= 1 else 2.0 if inc <= 3 else 5.0

# ---------- Загрузка движков ----------
def init_engines():
    global engines
    # Stockfish
    print("Загрузка Stockfish 18...")
    sf = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    sf.configure({"Skill Level": 20, "Hash": 256, "Threads": 2})
    engines['stockfish'] = sf

    # Berserk (если есть)
    if os.path.exists(BERSERK_PATH):
        try:
            print("Загрузка Berserk...")
            be = chess.engine.SimpleEngine.popen_uci(BERSERK_PATH)
            be.configure({"Skill Level": 20, "Hash": 128, "Threads": 1})
            engines['berserk'] = be
        except Exception as e:
            print(f"Berserk не загружен: {e}")

    # Clover (если есть)
    if os.path.exists(CLOVER_PATH):
        try:
            print("Загрузка Clover...")
            ce = chess.engine.SimpleEngine.popen_uci(CLOVER_PATH)
            ce.configure({"Skill Level": 20, "Hash": 128, "Threads": 1})
            engines['clover'] = ce
        except Exception as e:
            print(f"Clover не загружен: {e}")

    print(f"Загружено движков: {len(engines)}")

# ---------- Голосование ----------
def get_best_move(board, move_time):
    candidates = {}
    for name, eng in engines.items():
        try:
            analysis = eng.analyse(board, chess.engine.Limit(time=move_time))
            move = analysis.get('pv')[0] if analysis.get('pv') else None
            if not move:
                continue
            score = analysis.get('score')
            if score.is_mate():
                weight = 10000 if score.mate() > 0 else -10000
            else:
                w = score.white().score() if board.turn == chess.WHITE else -score.white().score()
                weight = max(-500, min(500, w)) / 100.0
            candidates[move.uci()] = candidates.get(move.uci(), []) + [weight]
        except Exception as e:
            print(f"Ошибка {name}: {e}")
    if not candidates:
        return None
    best_move = max(candidates.items(), key=lambda x: sum(x[1])/len(x[1]))
    print(f"Голосование: {best_move[0]} (средняя оценка {sum(best_move[1])/len(best_move[1]):.2f})")
    return best_move[0]

def make_move_with_retry(game_id, board, move_time):
    for attempt in range(3):
        try:
            move_uci = get_best_move(board, move_time)
            if not move_uci:
                return False
            client.bots.make_move(game_id, move_uci)
            print(f"[{game_id}] >>> {move_uci} ({move_time:.1f}s)")
            sys.stdout.flush()
            return True
        except Exception as e:
            print(f"[{game_id}] Попытка {attempt+1} не удалась: {e}")
            time.sleep(2**attempt)
    return False

# ---------- Игровой цикл ----------
def play_game(game_id, initial_fen):
    global active_games
    with games_lock:
        active_games += 1
    try:
        board = chess.Board(initial_fen) if initial_fen else chess.Board()
        my_id = client.account.get()['id']
        white_id = black_id = None
        move_time = 5.0
        while True:
            try:
                stream = client.bots.stream_game_state(game_id)
                for event in stream:
                    if 'clock' in event:
                        move_time = get_move_time(event['clock'])

                    if event['type'] == 'gameFull':
                        white_id = event.get('white', {}).get('id')
                        black_id = event.get('black', {}).get('id')
                        if white_id == my_id:
                            opponent = black_id
                        else:
                            opponent = white_id
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
            active_games -= 1

# ---------- Ручной вызов ----------
@app.get("/challenge/{username}")
def manual_challenge(username: str):
    try:
        client.challenges.create(username=username, rated=True, clock_limit=300, clock_increment=3, color="random")
        return {"status": "ok", "message": f"Challenge sent to {username}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------- Основной цикл ----------
def run_bot():
    init_engines()
    print("Бот запущен. Ожидание вызовов...")
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
        except Exception as e:
            print(f"Ошибка в главном цикле: {e}. Пауза 30 сек.")
            time.sleep(30)

threading.Thread(target=run_bot, daemon=True).start()
