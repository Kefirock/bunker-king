import itertools
from typing import Optional


class ProxyManager:
    def __init__(self, filepath: str = "proxies.txt"):
        self.filepath = filepath
        self.proxy_iterator = None
        self.load_proxies()

    def load_proxies(self):
        """Считывает прокси из файла и создает бесконечный итератор."""
        proxies = []
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    # Пропускаем пустые строки и комментарии
                    if not line or line.startswith("#"):
                        continue
                    formatted = self._format_proxy(line)
                    if formatted:
                        proxies.append(formatted)
        except FileNotFoundError:
            print(f"⚠️ Файл {self.filepath} не найден.")

        if not proxies:
            self.proxy_iterator = None
            print("⚠️ Список прокси пуст. Будет использовано прямое соединение.")
        else:
            # itertools.cycle создает бесконечный цикл (ротацию)
            self.proxy_iterator = itertools.cycle(proxies)
            print(f"✅ Успешно загружено SOCKS5 прокси: {len(proxies)}")

    def _format_proxy(self, raw_line: str) -> str:
        """
        Преобразует строку в формат socks5://user:pass@ip:port
        Поддерживает форматы:
        1. ip:port:user:pass
        2. ip:port
        3. user:pass@ip:port
        4. socks5://... (если уже указано)
        """
        # Если пользователь уже указал протокол, оставляем как есть
        if "://" in raw_line:
            return raw_line

        parts = raw_line.split(":")

        # Формат: ip:port (2 части)
        if len(parts) == 2:
            return f"socks5://{parts[0]}:{parts[1]}"

        # Формат: ip:port:user:pass (4 части) - самый частый для покупных прокси
        elif len(parts) == 4:
            ip, port, user, password = parts
            return f"socks5://{user}:{password}@{ip}:{port}"

        # Если формат user:pass@ip:port (специфичный)
        elif "@" in raw_line:
            return f"socks5://{raw_line}"

        # Если не поняли формат, возвращаем как есть, надеясь на чудо
        return f"socks5://{raw_line}"

    def get_next_proxy(self) -> Optional[str]:
        """Возвращает следующий прокси из списка или None."""
        if self.proxy_iterator:
            return next(self.proxy_iterator)
        return None