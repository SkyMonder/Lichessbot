import os
import time
import threading
import chess
import chess.engine
import requests
import berserk
from flask import Flask
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- Flask приложение (чтобы Render видел порт) ----------
app = Flask(__name__)

@app.route("/ping")
def ping():
    return "pong"

# ---------- Конфигурация ----------
API_TOKEN = os.environ["LICHESS_API_TOKEN"]
THINKER_URLS = [
    os.environ.get("THINKER1_URL", "https://stboch.onrender.com"),
    os.environ.get("THINKER2_URL", "https://cloche.onrender.com"),
    os.environ.get("THINKER3_URL", "https://brasche.onrender.com"),
]

# ---------- Расчёт времени на ход ----------
def calc_move_time(time_left_ms):
    t = time_left_ms / 1000.0
    if t > 30:
        return 0.3
    elif t > 10:
        return 0.2
    elif t > 5:
        return 0.1
    elif t > 1:
        return max(0.05, t * 0.02)
    else:
        return 0.01

# ---------- Запрос к мыслителю ----------
def query_thinker(url, fen, movetime):
    try:
        resp = requests.post(
            f"{url}/analyze",
            json={"fen": fen, "movetime": movetime},
            timeout=movetime + 2
        )
        data = resp.json()
        return data.get("move"), data.get("score", 0)
    except Exception:
        return None, None

# ---------- Выбор лучшего хода голосованием ----------
def choose_best_move(fen, movetime):
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(query_thinker, url, fen, movetime) for url in THINKER_URLS]
        results = []
        for f in as_completed(futures):
            move, score = f.result()
            if move:
                results.append((move, score))

    if not results:
        return None

    moves = [r[0] for r in results]
    # Мажоритарное голосование
    for m in set(moves):
        if moves.count(m) >= 2:
            return m
    # Иначе ход с наивысшей оценкой
    return max(results, key=lambda r: r[1])[0]

# ---------- Основной цикл бота ----------
def run_bot():
    session = berserk.TokenSession(API_TOKEN)
    client = berserk.Client(session=session)
    colours = {}  # game_id -> цвет бота

    print("🤖 Бот запущен, жду вызовов...")
    for event in client.board.stream_incoming_events():
        try:
            if event["type"] == "challenge":
                # Принимаем вызов
                client.board.accept_challenge(event["challenge"]["id"])
                print(f"✅ Принят вызов от {event['challenge']['challenger']['name']}")

            elif event["type"] == "gameStart":
                gid = event["game"]["id"]
                colours[gid] = event["game"]["color"]
                print(f"🎮 Игра {gid}, я играю {colours[gid]}")

            elif event["type"] == "gameState":
                gid = event["id"]
                if gid not in colours:
                    continue

                state = event
                if state.get("isMyTurn"):
                    colour = colours[gid]
                    # Моё время на часах
                    my_time = state.get("wtime" if colour == "white" else "btime", 0)

                    # Восстанавливаем доску по списку ходов
                    moves = state.get("moves", "")
                    board = chess.Board()
                    for m in moves.split():
                        board.push_uci(m)
                    fen = board.fen()

                    # Время на обдумывание
                    move_time = calc_move_time(my_time)
                    best_move = choose_best_move(fen, move_time)

                    if best_move:
                        client.board.make_move(gid, best_move)
                        print(f"♟️ Ход {best_move} за {move_time:.2f}с (осталось {my_time/1000:.1f}с)")
                    else:
                        # Аварийный ход
                        legal = list(board.legal_moves)
                        if legal:
                            fallback = str(legal[0])
                            client.board.make_move(gid, fallback)
                            print(f"⚠️ Аварийный ход {fallback}")
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(1)

# ---------- Запуск всего ----------
if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

    # Стартуем Flask (он слушает порт, Render будет доволен)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
