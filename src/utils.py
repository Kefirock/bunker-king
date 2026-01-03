import random
from typing import List, Union, Dict
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from src.config import cfg
from src.schemas import PlayerProfile, Persona, GameState


class GameSetup:
    @staticmethod
    def generate_players(user_data: Union[str, List[Dict]]) -> List[PlayerProfile]:
        scenarios = cfg.scenarios
        profs = scenarios["professions"][:]
        traits = scenarios["traits"][:]
        names = scenarios["bot_names"][:]
        personalities_data = scenarios.get("personalities", [])
        bot_models = cfg.models["player_models"]

        random.shuffle(profs)
        random.shuffle(traits)
        random.shuffle(names)

        players = []

        humans_to_create = []
        if isinstance(user_data, str):
            humans_to_create.append({"name": user_data})
        elif isinstance(user_data, list):
            humans_to_create = user_data

        target_total = cfg.gameplay.get("setup", {}).get("total_players", 5)
        human_count = len(humans_to_create)
        bots_needed = max(0, target_total - human_count)

        for i in range(bots_needed):
            bot_name = names.pop() if names else f"Bot-{i + 1}"
            bot_prof = profs.pop() if profs else "Выживший"
            bot_trait = traits.pop() if traits else "Обычный"

            p_data = random.choice(personalities_data)
            persona = Persona(
                id=p_data["id"],
                description=p_data["description"],
                style_example="",
                multipliers=p_data.get("multipliers", {})
            )

            players.append(PlayerProfile(
                name=bot_name,
                profession=bot_prof,
                trait=bot_trait,
                personality=persona,
                is_human=False,
                llm_config=random.choice(bot_models)
            ))

        human_persona = Persona(id="human", description="Игрок", style_example="", multipliers={})

        for h in humans_to_create:
            p_name = h["name"]
            display_name = f"{p_name} (Вы)" if isinstance(user_data, str) else p_name

            hum_prof = profs.pop() if profs else "Счастливчик"
            hum_trait = traits.pop() if traits else "Живой"

            players.append(PlayerProfile(
                name=display_name,
                profession=hum_prof,
                trait=hum_trait,
                personality=human_persona,
                is_human=True,
            ))

        random.shuffle(players)
        return players

    @staticmethod
    def init_game_state() -> GameState:
        catastrophes = cfg.scenarios["catastrophes"]
        scenario = random.choice(catastrophes)
        topic = scenario["topics"][0]

        return GameState(
            round=1,
            phase="presentation",
            topic=f"{scenario['name']}: {topic}",
            history=[]
        )

    @staticmethod
    def get_display_name(p: PlayerProfile, round_num: int, reveal_all: bool = False) -> str:
        """
        Формирует строку для отображения игрока в списке.
        reveal_all: Если True (конец игры), показывает всё.
        """
        visibility_rules = cfg.get_visibility(round_num)

        prof = p.profession if p.profession else "???"

        # Статус (жив/мертв)
        prefix = "" if p.is_alive else "💀 "

        if reveal_all:
            # Полное раскрытие в конце
            role_info = "Импостор" if p.status == "IMPOSTOR" else p.status
            return f"{prefix}<b>{p.name}</b>: {prof}, {p.trait} [{role_info}]"

        # Обычный режим (Туман войны)
        trait = "???"
        if visibility_rules.get("show_trait", False):
            trait = p.trait

        status_marker = " (Изгнан)" if not p.is_alive else ""

        return f"{prefix}<b>{p.name}</b>: {prof} [{trait}]{status_marker}"

    @staticmethod
    def generate_dashboard(game_state: GameState, players: List[PlayerProfile], viewer_name: str = None) -> str:
        """
        Генерирует текст для Закрепленного сообщения (Dashboard).
        viewer_name: Имя игрока, который смотрит (чтобы показать ему ЕГО данные полностью).
        """
        gs = game_state

        # Шапка
        phase_map = {
            "presentation": "ПРЕДСТАВЛЕНИЕ",
            "discussion": "ОБСУЖДЕНИЕ",
            "voting": "ГОЛОСОВАНИЕ",
            "runoff": "ПЕРЕСТРЕЛКА"
        }
        phase_name = phase_map.get(gs.phase, gs.phase.upper())

        # Выделяем тему как цитату
        header = (
            f"🔔 <b>РАУНД {gs.round}</b> | ФАЗА: {phase_name}\n"
            f"<blockquote>{gs.topic}</blockquote>\n\n"
            f"👥 <b>СПИСОК ВЫЖИВШИХ:</b>\n"
        )

        # Список всех
        list_str = ""
        viewer_profile = None

        for p in players:
            # Сохраняем профиль смотрящего для футера
            if viewer_name and p.name.startswith(viewer_name):
                viewer_profile = p
            elif viewer_name and viewer_name in p.name:  # Fallback для имен типа "Bob (Вы)"
                viewer_profile = p

            list_str += f"- {GameSetup.get_display_name(p, gs.round)}\n"

        # Футер (Личное досье)
        footer = ""
        if viewer_profile:
            factors = ", ".join([f"{k}:{v}" for k, v in viewer_profile.active_factors.items()])
            factors_str = f"\n⚠️ Факторы: {factors}" if factors else ""

            footer = (
                f"\n━━━━━━━━━━━━━━━━━━━\n"
                f"👤 <b>ВАШЕ ДОСЬЕ (Видно только вам):</b>\n"
                f"Профессия: <b>{viewer_profile.profession}</b>\n"
                f"Черта: <b>{viewer_profile.trait}</b>\n"
                f"Здоровье: {'Жив' if viewer_profile.is_alive else 'Мертв'}"
                f"{factors_str}"
            )

        return header + list_str + footer

    @staticmethod
    def generate_game_report(players: List[PlayerProfile]) -> str:
        """Финал игры: показывает все скрытые роли."""
        report = "🏁 <b>ИГРА ОКОНЧЕНА. ИТОГИ:</b>\n\n"

        survivors = [p for p in players if p.is_alive]
        dead = [p for p in players if not p.is_alive]

        report += "🏆 <b>ВЫЖИВШИЕ:</b>\n"
        if not survivors:
            report += "Никого...\n"
        for p in survivors:
            report += f"- {GameSetup.get_display_name(p, 999, reveal_all=True)}\n"

        report += "\n💀 <b>ПОГИБШИЕ:</b>\n"
        for p in dead:
            report += f"- {GameSetup.get_display_name(p, 999, reveal_all=True)}\n"

        return report

    @staticmethod
    def get_turn_keyboard(phase: str) -> ReplyKeyboardMarkup:
        """Возвращает клавиатуру с подсказками в зависимости от фазы."""
        buttons = []

        if phase == "presentation":
            buttons = [
                [KeyboardButton(text="👤 Представиться"), KeyboardButton(text="💼 О профессии")]
            ]
        elif phase == "discussion":
            buttons = [
                [KeyboardButton(text="🛡 Защититься"), KeyboardButton(text="⚔️ Атаковать")],
                [KeyboardButton(text="🤝 Поддержать"), KeyboardButton(text="❓ Задать вопрос")]
            ]
        elif phase == "runoff":
            buttons = [
                [KeyboardButton(text="🗣 Финальная речь")]
            ]

        if not buttons:
            return None

        return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True, one_time_keyboard=True,
                                   input_field_placeholder="Ваш ход...")

    @staticmethod
    def get_template_text(btn_text: str, player: PlayerProfile) -> str:
        """Возвращает шаблон текста при нажатии кнопки."""
        if "Представиться" in btn_text:
            return f"Всем привет. Я {player.name}, и я..."
        if "О профессии" in btn_text:
            return f"Я работаю как {player.profession}. В бункере это полезно тем, что..."
        if "Защититься" in btn_text:
            return "Я не согласен с обвинениями. Моя польза очевидна: ..."
        if "Атаковать" in btn_text:
            return "Меня смущает поведение... Мне кажется, он скрывает..."
        if "Поддержать" in btn_text:
            return "Я согласен с аргументами..."
        if "Задать вопрос" in btn_text:
            return "У меня вопрос к..."
        if "Финальная речь" in btn_text:
            return "Вы совершаете ошибку. Я должен остаться, потому что..."
        return ""