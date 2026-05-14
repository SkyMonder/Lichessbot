import os
import time
import requests
import berserk
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------- КОНФИГ ----------
API_TOKEN = os.environ["LICHESS_API_TOKEN"]  # секретный токен бота
THINKER_URLS = [
    os.environ.get("THINKER1_URL", "https://stboch.onrender.com"),
    os.environ.get("THINKER2_URL", "https://cloche.onrender.com"),
    os.environ.get("THINKER3_URL", "https://brasche.onrender.com"),
]
MAX_THINK_TIME = 5.0  # максимальное время на ход в секундах (защита)

# ---------- ВРЕМЯ НА ХОД ----------
def calculate_move_time(game_state):
    """
    Возвращает время на обдумывание в секундах, исходя из оставшегося времени.
    Правила:
      - пуля (начальное <= 60с): 0.3 сек при >30 сек, далее линейно до 0.01 сек
      - если осталась 1 сек → 0.01 сек
    """
    # game_state – словарь с полями wtime, btime, winc, binc, speed (приблизительно)
    # Берём свои часы (бот всегда играет за белых? Нет, надо определить)
    # Мы знаем цвет из event'а. Предположим, функция получает оставшееся время в мс и инкремент.
    wtime = game_state.get("wtime", 60000)
    btime = game_state.get("btime", 60000)
    winc = game_state.get("winc", 0)
    binc = game_state.get("binc", 0)
    speed = game_state.get("speed", "classical")

    # Определим цвет бота – здесь мы будем передавать уже вычисленное значение,
    # поэтому упростим: функция принимает time_left_ms и inc_ms.
    time_left_ms = game_state["time_left_ms"]
    inc_ms = game_state["inc_ms"]

    time_left = max(time_left_ms / 1000.0, 0.01)
    inc = inc_ms / 1000.0

    if speed == "bullet":   # начальное время <= 60с
        if time_left > 30:
            move_time = 0.3
        elif time_left > 10:
            move_time = max(0.1, time_left * 0.03)
        elif time_left > 1:
            move_time = max(0.05, time_left * 0.02)
        else:
            move_time = 0.01
    elif speed == "blitz":  # <= 180с, например
        if time_left > 60:
            move_time = 0.5
        elif time_left > 20:
            move_time = 0.3
        elif time_left > 5:
            move_time = max(0.1, time_left * 0.02)
        else:
            move_time = 0.05
    else:  # rapid/classical
        if time_left > 60:
            move_time = 1.0
        else:
            move_time = max(0.2, time_left * 0.05)

    # Не тратим больше 90% оставшегося времени
    move_time = min(move_time, time_left * 0.9)
    return round(move_time, 3)

# ---------- ГОЛОСОВАНИЕ ----------
def get_best_move_from_thinkers(fen: str, movetime: float) -> str:
    """Параллельно опрашивает мыслителей и выбирает ход большинством голосов."""
    def query_thinker(url):
        try:
            resp = requests.post(
                f"{url}/analyze",
                json={"fen": fen, "movetime": movetime},
                timeout=movetime + 2,
            )
            data = resp.json()
            move = data.get("move")
            score = data.get("score", 0)
            return move, score
        except Exception:
            return None, None

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(query_thinker, url) for url in THINKER_URLS]
        results = []
        for future in as_completed(futures):
            res = future.result()
            if res[0] is not None:
                results.append(res)

    if not results:
        return None  # все упали – аварийный ход

    # Голосование
    moves = [r[0] for r in results]
    # Если 2 из 3 совпали – берём этот ход
    for move in set(moves):
        if moves.count(move) >= 2:
            return move

    # Иначе выбираем ход с максимальной средней оценкой
    best_move = max(results, key=lambda x: x[1])[0]
    return best_move

# ---------- ОСНОВНОЙ ЦИКЛ ----------
def main():
    session = berserk.TokenSession(API_TOKEN)
    client = berserk.Client(session=session)

    print("Бот запущен и слушает вызовы...")
    for event in client.board.stream_incoming_events():
        if event["type"] == "challenge":
            try:
                client.board.accept_challenge(event["challenge"]["id"])
                print(f"Принят вызов от {event['challenge']['challenger']['name']}")
            except Exception as e:
                print(f"Ошибка принятия: {e}")
                continue

        elif event["type"] == "gameStart":
            game_id = event["game"]["id"]
            print(f"Игра началась: {game_id}")

            # Поток событий игры
            for game_event in client.board.stream_game_state(game_id):
                if game_event["type"] == "gameState":
                    state = game_event
                    # Проверяем, наш ли ход
                    if state["isMyTurn"]:
                        # Извлекаем время
                        wtime = state.get("wtime")
                        btime = state.get("btime")
                        winc = state.get("winc")
                        binc = state.get("binc")
                        # Бот всегда играет чёрными? Нет, надо знать цвет.
                        # Упростим: определим цвет по последнему ходу? В berserk есть поле "isMyTurn" и цвет из game.
                        # Возьмём из game_event["game"]["color"]? Но в gameStart есть colour.
                        # Запомним цвет при старте игры. Используем глобальный словарь.
                        # Для простоты в этом примере предположим, что бот играет чёрными.
                        # Чтобы сделать универсально, лучше хранить colour в объекте игры.
                        # Но в рамках ответа покажу вычисление на основе wtime/btime.
                        # Предположим, мы знаем цвет (white/black).
                        # Я добавлю хранение colour по game_id.
                        # Упростим: используем состояние из game_start, которое мы получили ранее.
                        # Это не идеально, но для демонстрации сойдёт.
                        # На практике нужно хранить colour в словаре при gameStart.
                        # Допустим, мы это сделали выше (опущено для краткости).
                        # Пока просто возьмём wtime, если белые, и btime, если чёрные.
                        # Цвет определим так: если state["isMyTurn"] и последний ход был от соперника...
                        # Но легче передать в функцию время в зависимости от цвета.
                        # Пойдём простым путём: запомним цвет при gameStart, сохраним в словарь colours.
                        pass  # см. ниже доработанный фрагмент
