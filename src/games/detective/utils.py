from typing import List, Dict
from src.core.schemas import BasePlayer
from src.games.detective.schemas import Fact, DetectivePlayerProfile


class DetectiveUtils:
    @staticmethod
    def get_public_board_text(scenario_title: str, public_facts: List[Fact]) -> str:
        """Общая доска расследования"""
        header = f"🕵️‍♂️ <b>ДЕЛО: {scenario_title}</b>\n\n"

        if not public_facts:
            return header + "📂 <i>Улик пока нет. Допрашивайте друг друга!</i>"

        lines = ["<b>⚡ ВСКРЫТЫЕ ФАКТЫ:</b>"]
        for f in public_facts:
            lines.append(f"🔹 <b>{f.type}:</b> {f.text}")

        return header + "\n".join(lines)

    @staticmethod
    def get_private_dashboard(player: BasePlayer, all_facts: Dict[str, Fact]) -> str:
        """Личное сообщение игрока (Role + Suggestions)"""
        prof: DetectivePlayerProfile = player.attributes.get("detective_profile")
        if not prof: return "Загрузка..."

        # 1. Шапка профиля
        role_icon = "🔪" if prof.role == "KILLER" else "🔍"
        text = (
            f"{role_icon} <b>ТВОЯ РОЛЬ: {prof.role}</b>\n"
            f"📜 <i>{prof.bio}</i>\n"
            f"🎯 Цель: {prof.secret_objective}\n\n"
        )

        # 2. Счетчик обязательных фактов
        done = prof.published_facts_count
        status = "✅ Выполнено" if done >= 2 else f"⚠️ Нужно вскрыть еще: {2 - done}"
        text += f"📊 <b>Вклад в расследование:</b> {status}\n\n"

        # 3. Суфлер (Копи-паста)
        sugg = prof.last_suggestions
        if sugg:
            text += "💡 <b>МЫСЛИ (Нажми, чтобы скопировать):</b>\n"
            if sugg.logic_text:
                text += f"🧠 Логика: <code>{sugg.logic_text}</code>\n"
            if sugg.defense_text:
                text += f"🛡️ Защита: <code>{sugg.defense_text}</code>\n"
            if sugg.bluff_text:
                text += f"🎭 Блеф: <code>{sugg.bluff_text}</code>\n"
        else:
            text += "💡 <i>Анализирую чат...</i>\n"

        text += "\n👇 <b>ТВОИ УЛИКИ (Инвентарь):</b>"
        return text

    @staticmethod
    def get_inventory_keyboard(player: BasePlayer, all_facts: Dict[str, Fact]) -> List[Dict]:
        """Генерация кнопок для вскрытия фактов"""
        prof: DetectivePlayerProfile = player.attributes.get("detective_profile")
        kb = []

        # Кнопка обновления мыслей
        kb.append({"text": "🔄 Обновить мысли", "callback_data": "refresh_suggestions"})

        # Кнопки фактов
        for fid in prof.inventory:
            fact = all_facts.get(fid)
            if fact and not fact.is_public:
                # Обрезаем текст для кнопки
                short_text = (fact.text[:20] + '..') if len(fact.text) > 20 else fact.text
                btn_text = f"📤 Вскрыть: {short_text}"
                kb.append({"text": btn_text, "callback_data": f"reveal_{fid}"})

        return kb