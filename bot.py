import os, sys, threading, time, random, traceback
import chess, berserk, requests
from fastapi import FastAPI, HTTPException
from collections import Counter

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

def get_move_time(clock, board):
    """Щедрое распределение времени для сильной игры."""
    inc = clock.get('increment', 0)
    my_time = clock.get('white' if board.turn == chess.WHITE else 'black', 0)
    moves_done = board.fullmove_number

    # Аварийный случай
    if my_time < 1.0:
        return 0.05

    # Пуля (инкремент 0-1) – всё равно быстро
    if inc <= 1:
        if my_time < 3.0:
            return 0.2
        return 0.5 if moves_done < 30 else 0.3

    # Блиц (инкремент 2-3) – умеренно
    if inc <= 3:
        if my_time < 10.0:
            return 0.8
        return 2.5 if moves_done < 25 else 1.5

    # Рапид / классика (инкремент ≥4) – глубоко
    if my_time < 20.0:
        return 1.5
    if moves_done < 40:
        return 5.0
    if moves_done < 80:
        return 3.0
    return 2.0

def get_best_move(fen, move_time):
    """Опрашивает три движка, возвращает ход с большинством голосов."""
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
    if not candidates:
        return None
    most_common = Counter(candidates).most_common(1)[0][0]
    print(f"Голосование: {most_common} (из {len(candidates)})")
    return most_common

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
        if len(active_games) >= MAX_CONCURRENT_GAMES or game_id in active_games:
            print(f"[{game_id}] Отклонено (активных игр {len(active_games)})")
            return
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
                        moves = event.get('state', {}).get('moves', '')
                        if moves:
                            for m in moves.split():
                                board.push_uci(m)
                        print(f"[{game_id}] gameFull: white={white_id} black={black_id} turn={board.turn} moves={len(board.move_stack)}")
                        opponent = black_id if white_id == my_id else white_id
                        send_greeting(game_id, opponent)
                    elif event['type'] == 'gameState':
                        moves = event.get('moves', '')
                        if moves:
                            mlist = moves.split()
                            while len(mlist) > len(board.move_stack):
                                board.push_uci(mlist[len(board.move_stack)])
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

@app.get("/challenge/{username}")
def manual_challenge(username: str):
    if not username:
        raise HTTPException(status_code=400, detail="Username required")
    try:
        client.challenges.create(username=username, rated=True, clock_limit=300, clock_increment=3, color="random", variant="standard")
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
                    if len(active_games) >= MAX_CONCURRENT_GAMES:
                        print(f"Отклонён вызов {ch['challenger']['id']}: много игр ({len(active_games)})")
                        continue
                    print(f"Вызов от {ch['challenger']['id']} принят")
                    client.bots.accept_challenge(ch['id'])
                    threading.Thread(target=play_game, args=(ch['id'], ch.get('initialFen')), daemon=True).start()
                elif event['type'] == 'gameStart':
                    game = event['game']
                    if game['id'] in active_games:
                        continue
                    print(f"Игра {game['id']} началась (после нашего вызова)")
                    threading.Thread(target=play_game, args=(game['id'], game.get('initialFen')), daemon=True).start()
        except Exception as e:
            print(f"Ошибка в главном цикле: {e}. Пауза 30 сек.")
            time.sleep(30)

threading.Thread(target=run_bot, daemon=True).start()
