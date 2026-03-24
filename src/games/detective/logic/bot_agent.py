import random
from typing import List, Dict, Any, Optional
from src.core.llm import llm_client
from src.core.config import core_cfg
from src.core.schemas import BasePlayer
from src.games.detective.schemas import Fact, DetectivePlayerProfile, RoleType, BotState, BotEmotion
from src.games.detective.config import detective_cfg


class DetectiveBotAgent:
    def __init__(self):
        self.bot_states: Dict[int, BotState] = {}  # bot_id -> BotState
    
    def _get_or_create_state(self, bot: BasePlayer) -> BotState:
        """Получить или создать состояние для бота"""
        if bot.id not in self.bot_states:
            # Проверяем, есть ли состояние в профиле
            prof = bot.attributes.get("detective_profile")
            if prof and prof.bot_state:
                self.bot_states[bot.id] = prof.bot_state
            else:
                self.bot_states[bot.id] = BotState()
        return self.bot_states[bot.id]
    
    def _update_emotions(self, state: BotState, history: List[str], 
                         public_facts: List[Fact], bot_char_name: str) -> None:
        """Обновить эмоции бота на основе событий"""
        emotion = state.emotion
        
        # Анализируем последнее сообщение
        if history:
            last_msg = history[-1].lower()
            
            # Если бота обвиняют
            if bot_char_name.lower() in last_msg:
                accusation_words = ["подозреваю", "обвиняю", "это ты", "виноват", "убийца"]
                if any(word in last_msg for word in accusation_words):
                    emotion.fear = min(1.0, emotion.fear + 0.2)
                    emotion.confidence = max(0.0, emotion.confidence - 0.1)
                    emotion.aggression = min(1.0, emotion.aggression + 0.1)
            
            # Если бота защищают
            defense_words = ["согласен", "поддерживаю", "не думаю", "невинен"]
            if any(word in last_msg for word in defense_words):
                if bot_char_name.lower() in last_msg:
                    emotion.confidence = min(1.0, emotion.confidence + 0.1)
                    emotion.fear = max(0.0, emotion.fear - 0.1)
        
        # Постепенное затухание эмоций
        emotion.fear = max(0.0, emotion.fear - 0.02)
        emotion.aggression = max(0.0, emotion.aggression - 0.02)
        
        # Ограничиваем значения
        emotion.fear = min(1.0, max(0.0, emotion.fear))
        emotion.aggression = min(1.0, max(0.0, emotion.aggression))
        emotion.confidence = min(1.0, max(0.0, emotion.confidence))
    
    def _update_suspicion(self, state: BotState, history: List[str], 
                          all_players: List[BasePlayer]) -> None:
        """Обновить карту подозрений бота"""
        if not history:
            return
        
        last_msg = history[-1].lower()
        
        # Ищем имена персонажей в контексте обвинений
        accusation_patterns = ["подозреваю", "обвиняю", "это был", "виновен"]
        if any(pattern in last_msg for pattern in accusation_patterns):
            for player in all_players:
                char_name = player.attributes["detective_profile"].character_name
                if char_name.lower() in last_msg and char_name != state.emotion:
                    # Увеличиваем подозрение к упомянутому
                    current = state.suspicion_map.get(char_name, 0.5)
                    state.suspicion_map[char_name] = min(1.0, current + 0.15)
    
    def _get_emotion_instruction(self, emotion: BotEmotion) -> str:
        """Сгенерировать инструкцию на основе эмоций"""
        instructions = []
        
        if emotion.fear > 0.7:
            instructions.append("Ты НАПУГАН. Защищайся, уходи от ответов, не привлекай внимания.")
        elif emotion.fear > 0.4:
            instructions.append("Ты Встревожен. Будь осторожен в словах.")
        
        if emotion.aggression > 0.7:
            instructions.append("Ты АГРЕССИВЕН. Атакуй других, обвиняй, дави.")
        elif emotion.aggression > 0.4:
            instructions.append("Ты Насторожен. Задавай неудобные вопросы.")
        
        if emotion.confidence < 0.3:
            instructions.append("Ты НЕ УВЕРЕН в себе. Путайся, сомневайся, можешь проговориться.")
        elif emotion.confidence > 0.7:
            instructions.append("Ты УВЕРЕН в себе. Говори смело, настаивай на своём.")
        
        return " ".join(instructions) if instructions else "Действуй спокойно."
    
    def _save_statement(self, bot: BasePlayer, speech: str, fact_id: Optional[str],
                        all_players: List[BasePlayer]) -> None:
        """Сохранить заявление бота в историю и обновить отношения"""
        state = self._get_or_create_state(bot)
        
        # Сохраняем речь (ограничиваем длину)
        if speech and len(speech) > 10:
            state.made_statements.append(speech[:200])
            if len(state.made_statements) > 10:
                state.made_statements = state.made_statements[-10:]
            
            # Анализируем речь на наличие обвинений
            speech_lower = speech.lower()
            accusation_patterns = ["подозреваю", "обвиняю", "это был", "виновен", "это ты"]
            
            if any(pattern in speech_lower for pattern in accusation_patterns):
                # Ищем, кого обвиняют
                for player in all_players:
                    if player.id == bot.id:
                        continue
                    char_name = player.attributes["detective_profile"].character_name
                    if char_name.lower() in speech_lower:
                        # Этот бот обвиняет другого -> становимся врагами
                        state.relations[char_name] = "enemy"
                        state.accused_others.append(char_name)
        
        # Сохраняем вскрытый факт
        if fact_id:
            state.revealed_facts.append(fact_id)
    
    def _compress_history(self, history: List[str], max_items: int = 8) -> str:
        """Сжать историю, сохранив ключевые моменты"""
        if len(history) <= max_items:
            return "\n".join(history)
        
        # Берём первые и последние сообщения + случайные из середины
        compressed = history[:2] + history[-2:]
        import random
        if len(history) > 4:
            middle = random.sample(history[2:-2], min(4, len(history) - 4))
            compressed = history[:2] + middle + history[-2:]
        
        return "\n".join(compressed)

    async def make_turn(self,
                        bot: BasePlayer,
                        all_players: List[BasePlayer],
                        scenario_data: Dict,
                        history: List[str],
                        public_facts: List[Fact],
                        all_facts_map: Dict[str, Fact],
                        current_round: int,
                        max_rounds: int,
                        logger=None) -> Dict[str, Any]:

        prof: DetectivePlayerProfile = bot.attributes.get("detective_profile")
        if not prof: return {}

        # Получаем или создаём состояние бота
        state = self._get_or_create_state(bot)
        
        # Обновляем эмоции и подозрения
        self._update_emotions(state, history, public_facts, prof.character_name)
        self._update_suspicion(state, history, all_players)

        pub_str = "; ".join([f"[{f.type}] {f.text}" for f in public_facts]) or "Нет"

        inv_lines = []
        for fid in prof.inventory:
            fact = all_facts_map.get(fid)
            if fact and not fact.is_public:
                inv_lines.append(f"ID: {fid} | [{fact.type}] {fact.text}")

        inv_str = "\n".join(inv_lines) if inv_lines else "Пусто"
        
        # Формируем список прошлых заявлений
        statements_str = "; ".join(state.made_statements[-3:]) if state.made_statements else "Нет"
        
        # Формируем список отношений
        allies = [name for name, rel in state.relations.items() if rel == "ally"]
        enemies = [name for name, rel in state.relations.items() if rel == "enemy"]
        allies_str = ", ".join(allies[-3:]) if allies else "Нет"
        enemies_str = ", ".join(enemies[-3:]) if enemies else "Нет"
        
        # Получаем инструкцию по эмоциям
        emotion_instruction = self._get_emotion_instruction(state.emotion)

        # --- РЕАКТИВНОСТЬ ---
        # Проверяем, упоминали ли нас в последнем сообщении
        reaction_instruction = ""
        if history:
            last_msg = history[-1]
            if prof.character_name in last_msg:
                reaction_instruction = f"ВНИМАНИЕ: В последнем сообщении упомянули твое имя ({prof.character_name}). ТЫ ОБЯЗАН ОТВЕТИТЬ на это обращение!"

        # Выбираем промпт в зависимости от роли
        if prof.role == RoleType.KILLER:
            prompt_template = detective_cfg.prompts["bot_player"].get("killer")
            if not prompt_template:
                prompt_template = detective_cfg.prompts["bot_player"]["main"]
        else:
            prompt_template = detective_cfg.prompts["bot_player"]["main"]

        # Используем видимую причину смерти
        cause_to_show = scenario_data.get("apparent_cause", "Неизвестно")
        murder_method = scenario_data.get("method", "неизвестен")
        
        # Сжимаем историю для экономии токенов
        compressed_history = self._compress_history(history)

        prompt = prompt_template.format(
            name=bot.name,
            tag=prof.tag,
            character_name=prof.character_name,
            legend=prof.legend,
            objective=prof.secret_objective,
            secret=prof.secret_objective,  # Для убийцы — его тайна

            # Флаг нашедшего (влияет на первый ход)
            is_finder=prof.is_finder,

            scenario_title=scenario_data.get("title", ""),
            victim=scenario_data.get("victim_name", "Неизвестный"),
            cause=cause_to_show,
            murder_method=murder_method,

            public_facts=pub_str,
            inventory=inv_str,
            history=compressed_history + "\n" + reaction_instruction,
            published_count=prof.published_facts_count,
            current_round=current_round,
            max_rounds=max_rounds,
            
            # Новые поля для состояния
            emotion_state=emotion_instruction,
            past_statements=statements_str,
            allies=allies_str,
            enemies=enemies_str
        )

        model = core_cfg.models["player_models"][0]

        try:
            temp_boost = 0.2 * (current_round / max_rounds)
            temp = 0.7 + temp_boost

            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temp,
                json_mode=True,
                logger=logger
            )
            data = llm_client.parse_json(response)

            # Сохраняем заявление бота
            speech = data.get("speech", "")
            fact_id = data.get("reveal_fact_id")
            self._save_statement(bot, speech, fact_id, all_players)
            
            # Сохраняем состояние в профиль (для персистентности)
            prof.bot_state = state

            if logger:
                logger.log_event("BOT_DECISION", f"{bot.name} ({prof.character_name}) acted", {
                    **data, 
                    "emotion": state.emotion.dict()
                })

            return data
        except Exception as e:
            print(f"Bot Error {bot.name}: {e}")
            if logger: logger.log_event("BOT_ERROR", f"Error: {e}")
            return {"speech": "...", "reveal_fact_id": None}

    async def make_vote(self,
                        bot: BasePlayer,
                        candidates: List[BasePlayer],
                        scenario_data: Dict,
                        history: List[str],
                        public_facts: List[Fact],
                        logger=None) -> str:

        prof: DetectivePlayerProfile = bot.attributes.get("detective_profile")

        # Исключаем себя
        valid_candidates = [p for p in candidates if p.id != bot.id]
        if not valid_candidates:
            return bot.attributes['detective_profile'].character_name

        if prof.role == RoleType.KILLER:
            target = random.choice(valid_candidates)
            target_char = target.attributes['detective_profile'].character_name
            if logger: logger.log_event("BOT_VOTE", f"{bot.name} (KILLER) auto-voted against {target_char}")
            return target_char

        pub_str = "; ".join([f"{f.text}" for f in public_facts])

        cand_str = ", ".join([
            f"{p.attributes['detective_profile'].character_name}"
            for p in valid_candidates
        ])

        prompt_template = detective_cfg.prompts["bot_player"]["vote"]

        prompt = prompt_template.format(
            character_name=prof.character_name,
            scenario_title=scenario_data.get("title", ""),
            victim=scenario_data.get("victim_name", "Неизвестный"),
            public_facts=pub_str,
            history="\n".join(history[-15:]),
            candidates=cand_str
        )

        model = core_cfg.models["player_models"][0]
        try:
            response = await llm_client.generate(
                model_config=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                json_mode=True,
                logger=logger
            )
            data = llm_client.parse_json(response)
            target_char = data.get("vote_target_name", "")

            if logger: logger.log_event("BOT_VOTE_DECISION", f"{bot.name} voted against char {target_char}")

            if any(p.attributes['detective_profile'].character_name == target_char for p in valid_candidates):
                return target_char

            return random.choice(valid_candidates).attributes['detective_profile'].character_name

        except Exception as e:
            if logger: logger.log_event("BOT_VOTE_ERROR", str(e))
            return random.choice(valid_candidates).attributes['detective_profile'].character_name