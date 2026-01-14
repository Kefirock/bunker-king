import random
import string
from typing import Dict, List, Optional


class Lobby:
    def __init__(self, lobby_id: str, host_id: int, host_name: str):
        self.lobby_id = lobby_id
        self.host_id = host_id
        self.status = "waiting"  # "waiting", "playing"
        self.game_type = "bunker"

        # UI: Где находится сообщение меню
        self.menu_message_id: Optional[int] = None
        self.chat_id: int = 0

        # Список участников: {user_id: {name, id}}
        self.players: Dict[int, dict] = {}

        # Сразу добавляем хоста
        self.add_player(host_id, host_name)

    def add_player(self, user_id: int, name: str):
        """Добавляет только реальных людей"""
        if user_id in self.players:
            return
        self.players[user_id] = {
            "id": user_id,
            "name": name
        }

    def remove_player(self, user_id: int):
        if user_id in self.players:
            del self.players[user_id]

    def get_players_list_text(self) -> str:
        lines = []
        for pid, p in self.players.items():
            mark = " ⭐" if pid == self.host_id else ""
            lines.append(f"👤 <b>{p['name']}</b>{mark}")
        return "\n".join(lines)

    def to_game_users_list(self) -> List[dict]:
        return [{"id": p["id"], "name": p["name"]} for p in self.players.values()]


class LobbyManager:
    def __init__(self):
        self.lobbies: Dict[str, Lobby] = {}
        # user_id -> lobby_id
        self.user_to_lobby: Dict[int, str] = {}

    def create_lobby(self, host_id: int, host_name: str) -> Lobby:
        lid = ''.join(random.choices(string.ascii_uppercase, k=4))
        lobby = Lobby(lid, host_id, host_name)
        self.lobbies[lid] = lobby
        self.user_to_lobby[host_id] = lid
        return lobby

    def get_lobby(self, lobby_id: str) -> Optional[Lobby]:
        return self.lobbies.get(lobby_id)

    def get_all_waiting(self) -> List[Lobby]:
        """Возвращает список лобби, готовых к игре"""
        return [l for l in self.lobbies.values() if l.status == "waiting"]

    def join_lobby(self, lobby_id: str, user_id: int, user_name: str) -> bool:
        lobby = self.get_lobby(lobby_id)
        if not lobby or lobby.status != "waiting":
            return False

        lobby.add_player(user_id, user_name)
        self.user_to_lobby[user_id] = lobby_id
        return True

    def leave_lobby(self, user_id: int) -> Optional[Lobby]:
        lid = self.user_to_lobby.get(user_id)
        if not lid: return None

        lobby = self.get_lobby(lid)
        if lobby:
            lobby.remove_player(user_id)
            # Если вышел хост или пусто - удаляем
            if user_id == lobby.host_id or len(lobby.players) == 0:
                self.delete_lobby(lid)

        if user_id in self.user_to_lobby:
            del self.user_to_lobby[user_id]

        return lobby

    def delete_lobby(self, lobby_id: str):
        if lobby_id in self.lobbies:
            lobby = self.lobbies[lobby_id]
            for uid in list(lobby.players.keys()):
                if uid in self.user_to_lobby:
                    del self.user_to_lobby[uid]
            del self.lobbies[lobby_id]


lobby_manager = LobbyManager()