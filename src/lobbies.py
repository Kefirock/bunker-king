import random
import string
from typing import Dict, List, Optional
from src.schemas import GameState


class Lobby:
    def __init__(self, lobby_id: str, host_id: int, host_name: str):
        self.lobby_id = lobby_id
        self.host_id = host_id
        self.players: List[Dict] = []
        self.status = "waiting"

        # Состояние игры
        self.game_state: Optional[GameState] = None
        self.game_players = []
        self.current_turn_index = 0
        self.catastrophe_data = {}

        # --- НОВОЕ: Хранение голосов { "VoterName": "TargetName" } ---
        self.votes: Dict[str, str] = {}

        self.add_player(host_id, host_id, host_name)

    def add_player(self, user_id: int, chat_id: int, name: str):
        for p in self.players:
            if p["user_id"] == user_id:
                return
        self.players.append({"user_id": user_id, "chat_id": chat_id, "name": name})

    def remove_player(self, user_id: int):
        self.players = [p for p in self.players if p["user_id"] != user_id]


class LobbyManager:
    def __init__(self):
        self.lobbies: Dict[str, Lobby] = {}

    def create_lobby(self, host_id: int, host_name: str) -> Lobby:
        lid = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        lobby = Lobby(lid, host_id, host_name)
        self.lobbies[lid] = lobby
        return lobby

    def get_lobby(self, lobby_id: str) -> Optional[Lobby]:
        return self.lobbies.get(lobby_id)

    def get_all_waiting(self) -> List[Lobby]:
        return [l for l in self.lobbies.values() if l.status == "waiting"]

    def find_lobby_by_user(self, user_id: int) -> Optional[Lobby]:
        for lobby in self.lobbies.values():
            for p in lobby.players:
                if p["user_id"] == user_id:
                    return lobby
        return None

    def delete_lobby(self, lobby_id: str):
        if lobby_id in self.lobbies:
            del self.lobbies[lobby_id]


lobby_manager = LobbyManager()