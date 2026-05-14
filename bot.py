import os
import chess
import chess.engine
from flask import Flask, request, jsonify

app = Flask(__name__)

# Настройки из переменных окружения
STOCKFISH_PATH = os.environ.get("STOCKFISH_PATH", "stockfish")
SKILL_LEVEL = int(os.environ.get("SKILL_LEVEL", 20))
ENGINE_TIMEOUT = 60  # максимальное время работы движка в секундах

def analyze_fen(fen: str, movetime: float) -> dict:
    """Запускает Stockfish и возвращает лучший ход и оценку."""
    board = chess.Board(fen)
    if board.is_game_over():
        return {"move": None, "score": 0, "error": "game over"}

    # Запускаем движок с ограничением времени
    engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
    engine.configure({"Skill Level": SKILL_LEVEL})

    # movetime в секундах, переводим в секунды для limit
    limit = chess.engine.Limit(time=movetime)
    try:
        result = engine.play(board, limit)
        info = engine.analyse(board, limit)
        score = info["score"].white()  # оценка с точки зрения белых
        # Преобразуем оценку в число (сантипешки или мат)
        if score.is_mate():
            score_val = 10000 if score.mate() > 0 else -10000
        else:
            score_val = score.score()
        engine.quit()
        return {"move": result.move.uci(), "score": score_val}
    except Exception as e:
        engine.quit()
        return {"move": None, "score": 0, "error": str(e)}

@app.route("/ping")
def ping():
    return "pong"

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    fen = data.get("fen")
    movetime = data.get("movetime", 0.1)  # по умолчанию 0.1 сек
    if not fen:
        return jsonify({"error": "fen required"}), 400
    result = analyze_fen(fen, movetime)
    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
