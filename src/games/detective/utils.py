from typing import List, Dict
from src.core.schemas import BasePlayer
from src.games.detective.schemas import Fact, DetectivePlayerProfile, FactType, RoleType

ROLE_MAP = {
    RoleType.INNOCENT: "🕵️ Мирный житель",
    RoleType.KILLER: "🔪 Убийца"
}

FACT_TYPE_ICONS = {
    FactType.PHYSICAL: "🧤",
    FactType.TESTIMONY: "🗣",
    FactType.MOTIVE: "💔",
    FactType.ALIBI: "📍"
}

FACT_TYPE_NAMES = {
    FactType.PHYSICAL: "Вещдок",
    FactType.TESTIMONY: "Показания",
    FactType.MOTIVE: "Мотив",
    FactType.ALIBI: "Алиби"
}

BOT_NAMES_POOL = [
    "Доктор Мортимер", "Леди Эшли", "Полковник Мастард", "Мисс Скарлетт",
    "Профессор Плам", "Дворецкий Бэрримор", "Инспектор Лестрейд",
    "Графиня Валевска", "Капитан Гастингс", "Миссис Хадсон"
]


class DetectiveUtils:
    @staticmethod
    def get_bot_names(count: int) -> List[str]:
        import random
        return random.sample(BOT_NAMES_POOL, min(count, len(BOT_NAMES_POOL)))

    @staticmethod
    def get_public_board_text(scenario_title: str, public_facts: List[Fact]) -> str:
        header = f"📁 <b>ДЕЛО: {scenario_title}</b>\n"
        if not public_facts:
            return header + "\n<i>Доска улик пуста. Беседуйте, чтобы вскрыть правду!</i>"

        lines = ["\n<b>⚡ ВСКРЫТЫЕ ФАКТЫ:</b>"]
        for f in public_facts:
            icon = FACT_TYPE_ICONS.get(f.type, "📄")
            lines.append(f"{icon} <b>{f.keyword}:</b> {f.text}")
        return header + "\n".join(lines)

    @staticmethod
    def get_private_dashboard(player: BasePlayer, all_facts: Dict[str, Fact]) -> str:
        prof: DetectivePlayerProfile = player.attributes.get("detective_profile")
        if not prof: return "⏳ Загрузка..."

        role_str = ROLE_MAP.get(prof.role, str(prof.role))

        # БЛОК 1: Основное Досье
        text = (
            f"🎭 <b>ВАШ ПЕРСОНАЖ:</b> {prof.character_name}\n"
            f"🧠 <b>Характер:</b> {prof.archetype}\n"
            f"🏷 <b>Статус:</b> {role_str}\n"
            f"🎯 <b>Цель:</b> {prof.secret_objective}\n"
            f"📜 <b>Легенда:</b> <i>{prof.bio}</i>\n\n"
        )

        # БЛОК 2: Связи (НОВОЕ)
        if prof.relationships and prof.relationships != "Нет связей":
            text += f"🔗 <b>СВЯЗИ:</b>\n<i>{prof.relationships}</i>\n\n"

        # БЛОК 3: Статус участия
        done = prof.published_facts_count
        status = "✅ Выполнено" if done >= 2 else f"⚠️ Осталось вскрыть: <b>{2 - done}</b>"
        text += f"📊 <b>Вклад:</b> {status}\n\n"

        # БЛОК 4: Подсказки (Краткие)
        sugg = prof.last_suggestions
        if sugg:
            text += "💡 <b>СОВЕТЫ (Нажми, чтобы скопировать):</b>\n"
            if sugg.logic_text: text += f"🔹 <code>{sugg.logic_text}</code>\n"
            if sugg.defense_text: text += f"🛡 <code>{sugg.defense_text}</code>\n"
            if sugg.bluff_text: text += f"🎭 <code>{sugg.bluff_text}</code>\n"

        text += "\n👇 <b>ВАШ ИНВЕНТАРЬ:</b>\n<i>(Нажмите на кнопку, чтобы осмотреть улику перед публикацией)</i>"
        return text

    @staticmethod
    def get_inventory_keyboard(player: BasePlayer, all_facts: Dict[str, Fact]) -> List[Dict]:
        prof: DetectivePlayerProfile = player.attributes.get("detective_profile")
        kb = []

        for fid in prof.inventory:
            fact = all_facts.get(fid)
            if fact and not fact.is_public:
                icon = FACT_TYPE_ICONS.get(fact.type, "📄")
                btn_text = f"{icon} {fact.keyword}"
                kb.append({"text": btn_text, "callback_data": f"preview_{fid}"})

        if not kb:
            kb.append({"text": "📭 Карт нет / Лимит исчерпан", "callback_data": "dummy_empty"})

        return kb