from typing import List, Dict
from src.core.schemas import BasePlayer
from src.games.detective.schemas import Fact, DetectivePlayerProfile, FactType, RoleType

# --- КОНСТАНТЫ ПЕРЕВОДА ---

ROLE_MAP = {
    RoleType.INNOCENT: "🕵️ Мирный житель",
    RoleType.KILLER: "🔪 Убийца"
}

FACT_TYPE_ICONS = {
    FactType.PHYSICAL: "🧤",  # Улика
    FactType.TESTIMONY: "🗣",  # Показания
    FactType.MOTIVE: "💔",  # Мотив
    FactType.ALIBI: "📍"  # Алиби
}

FACT_TYPE_NAMES = {
    FactType.PHYSICAL: "Вещдок",
    FactType.TESTIMONY: "Показания",
    FactType.MOTIVE: "Мотив",
    FactType.ALIBI: "Алиби"
}


class DetectiveUtils:
    @staticmethod
    def get_public_board_text(scenario_title: str, public_facts: List[Fact]) -> str:
        """Общая доска расследования"""
        header = f"📁 <b>ДЕЛО: {scenario_title}</b>\n"

        if not public_facts:
            return header + "\n<i>Доска улик пуста. Беседуйте, чтобы вскрыть правду!</i>"

        lines = ["\n<b>⚡ ВСКРЫТЫЕ ФАКТЫ:</b>"]
        for f in public_facts:
            icon = FACT_TYPE_ICONS.get(f.type, "📄")
            name = FACT_TYPE_NAMES.get(f.type, "Факт")
            lines.append(f"{icon} <b>{name}:</b> {f.text}")

        return header + "\n".join(lines)

    @staticmethod
    def get_private_dashboard(player: BasePlayer, all_facts: Dict[str, Fact]) -> str:
        """Личное сообщение игрока (Role + Suggestions)"""
        prof: DetectivePlayerProfile = player.attributes.get("detective_profile")
        if not prof: return "⏳ Загрузка профиля..."

        # 1. Роль и Легенда
        role_str = ROLE_MAP.get(prof.role, str(prof.role))

        text = (
            f"<b>ВАШЕ ДОСЬЕ:</b>\n"
            f"{role_str}\n"
            f"<blockquote>{prof.bio}</blockquote>\n"
            f"🎯 <b>Личная цель:</b> {prof.secret_objective}\n\n"
        )

        # 2. Статус участия
        done = prof.published_facts_count
        needed = 2
        if done >= needed:
            status = "✅ Норма выполнена"
        else:
            status = f"⚠️ Нужно вскрыть еще: <b>{needed - done}</b>"

        text += f"📊 <b>Вклад в дело:</b> {status}\n"
        text += "<i>(Вы обязаны опубликовать минимум 2 факта за игру)</i>\n\n"

        # 3. Суфлер (Копи-паста)
        sugg = prof.last_suggestions
        if sugg:
            text += "💡 <b>ПОДСКАЗКИ (Нажми на текст):</b>\n"

            if sugg.logic_text and len(sugg.logic_text) > 5:
                text += f"🧠 Логика:\n<code>{sugg.logic_text}</code>\n"

            if sugg.defense_text and len(sugg.defense_text) > 5:
                text += f"🛡️ Защита:\n<code>{sugg.defense_text}</code>\n"

            if sugg.bluff_text and len(sugg.bluff_text) > 5:
                label = "🎭 Блеф" if prof.role == RoleType.KILLER else "🌪 Увод темы"
                text += f"{label}:\n<code>{sugg.bluff_text}</code>\n"
        else:
            text += "💡 <i>Слушаю разговор... (Нажми «Обновить мысли»)</i>\n"

        # 4. Инвентарь (Текстовое отображение)
        text += "\n👇 <b>ВАШИ КАРТЫ (Инвентарь):</b>"

        # Проверяем, есть ли факты вообще
        my_facts = [all_facts.get(fid) for fid in prof.inventory if all_facts.get(fid)]
        # Оставляем только те, что еще НЕ вскрыты (вскрытые уходят на общую доску)
        hidden_facts = [f for f in my_facts if not f.is_public]

        if not hidden_facts:
            text += "\n<i>(Пусто. Вы всё вскрыли или карт не было)</i>"

        return text

    @staticmethod
    def get_inventory_keyboard(player: BasePlayer, all_facts: Dict[str, Fact]) -> List[Dict]:
        """Генерация кнопок для вскрытия фактов"""
        prof: DetectivePlayerProfile = player.attributes.get("detective_profile")
        kb = []

        # 1. Кнопка суфлера
        kb.append({"text": "🔄 Обновить мысли", "callback_data": "refresh_suggestions"})

        # 2. Кнопки фактов
        count = 0
        for fid in prof.inventory:
            fact = all_facts.get(fid)
            # Показываем кнопку только если факт существует и СКРЫТ
            if fact and not fact.is_public:
                count += 1
                icon = FACT_TYPE_ICONS.get(fact.type, "📄")

                # Обрезаем текст, чтобы кнопка не была гигантской
                clean_text = fact.text.replace("\n", " ")
                short_text = (clean_text[:25] + '..') if len(clean_text) > 25 else clean_text

                btn_text = f"📤 Вскрыть: {icon} {short_text}"
                kb.append({"text": btn_text, "callback_data": f"reveal_{fid}"})

        if count == 0:
            # Декоративная кнопка, если нечего вскрывать
            kb.append({"text": "📭 Карт нет", "callback_data": "dummy_empty"})

        return kb