import berserk
import chess
import chess.engine
import time
import os
from fastapi import FastAPI
import threading

TOKEN = os.environ.get("LICHESS_TOKEN")  # токен из переменной окружения
STOCKFISH_PATH = "stockfish.exe"  # на Render будем использовать системный stockfish

app = FastAPI()

engine = None
client = None

@app.get("/health")
def health():
    return {"status": "ok"}

def run_bot():
    global engine, client
    session = berserk.TokenSession(TOKEN)
    client = berserk.Client(session)
    engine = chess.engine.SimpleEngine.popen_uci("/usr/games/stockfish")  # путь на Render
    
    print("Бот запущен. Ожидание вызовов...")
    for challenge in client.bots.stream_incoming_events():
        if challenge['type'] == 'challenge':
            client.bots.accept_challenge(challenge['challenge']['id'])
            print(f"Принят вызов от {challenge['challenge']['challenger']['id']}")
            game_id = challenge['challenge']['id']
            play_game(game_id, challenge['challenge']['initialFen'])

def play_game(game_id, initial_fen):
    stream = client.bots.stream_game_state(game_id)
    board = chess.Board(initial_fen)
    
    for event in stream:
        if event['type'] == 'gameFull':
            for move in event.get('state', {}).get('moves', '').split():
                board.push_uci(move)
        elif event['type'] == 'gameState':
            moves = event['moves'].split()
            while len(moves) > len(board.move_stack):
                board.push_uci(moves[len(board.move_stack)])
        
        if board.turn == chess.WHITE and event.get('white', {}).get('id') == client.account.get()['id']:
            make_move(game_id, board)
        elif board.turn == chess.BLACK and event.get('black', {}).get('id') == client.account.get()['id']:
            make_move(game_id, board)

def make_move(game_id, board):
    result = engine.play(board, chess.engine.Limit(time=2.0))
    if result.move:
        client.bots.make_move(game_id, result.move.uci())

# Запускаем бота в отдельном потоке
threading.Thread(target=run_bot, daemon=True).start()
