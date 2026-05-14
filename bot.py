import os
import chess
import requests
import berserk
from concurrent.futures import ThreadPoolExecutor, as_completed

API_TOKEN = os.environ["LICHESS_API_TOKEN"]
THINKER_URLS = [
    os.environ.get("THINKER1_URL", "https://stboch.onrender.com"),
    os.environ.get("THINKER2_URL", "https://cloche.onrender.com"),
    os.environ.get("THINKER3_URL", "https://brasche.onrender.com"),
]

def calc_move_time(time_left_ms):
    """Время на ход в секундах по заданному правилу."""
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

def query_thinker(url, fen, movetime):
    try:
        resp = requests.post(f"{url}/analyze",
                             json={"fen": fen, "movetime": movetime},
                             timeout=movetime + 2)
        data = resp.json()
        return data.get("move"), data.get("score", 0)
    except:
        return None, None

def choose_best_move(fen, movetime):
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(query_thinker, url, fen, movetime) for url in THINKER_URLS]
        results = []
        for f in as_completed(futures):
            res = f.result()
            if res[0]:
                results.append(res)
    if not results:
        return None
    moves = [r[0] for r in results]
    for m in set(moves):
        if moves.count(m) >= 2:
            return m
    return max(results, key=lambda r: r[1])[0]

def main():
    session = berserk.TokenSession(API_TOKEN)
    client = berserk.Client(session=session)
    colours = {}   # game_id -> цвет бота
    print("Бот запущен, жду соперников...")
    for event in client.board.stream_incoming_events():
        if event["type"] == "challenge":
            try:
                client.board.accept_challenge(event["challenge"]["id"])
                print(f"Принят вызов от {event['challenge']['challenger']['name']}")
            except Exception as e:
                print(f"Ошибка принятия вызова: {e}")

        elif event["type"] == "gameStart":
            gid = event["game"]["id"]
            colours[gid] = event["game"]["color"]
            print(f"Игра {gid} началась, цвет: {colours[gid]}")

        elif event["type"] == "gameState":
            state = event
            gid = state["id"]
            if gid not in colours:
                continue

            if state.get("isMyTurn"):
                colour = colours[gid]
                my_time = state.get("wtime" if colour == "white" else "btime", 0)
                # FEN из ходов
                moves = state.get("moves", "")
                board = chess.Board()
                for m in moves.split():
                    board.push_uci(m)
                fen = board.fen()

                move_time = calc_move_time(my_time)
                best_move = choose_best_move(fen, move_time)
                if best_move:
                    try:
                        client.board.make_move(gid, best_move)
                        print(f"Ход {best_move} за {move_time:.2f}с")
                    except Exception as e:
                        print(f"Ошибка хода: {e}")
                else:
                    # fallback – случайный легальный ход
                    legal = list(board.legal_moves)
                    if legal:
                        fallback = str(legal[0])
                        client.board.make_move(gid, fallback)
                        print(f"Аварийный ход {fallback}")

if __name__ == "__main__":
    main()
