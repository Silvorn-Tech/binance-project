class MarketData:
    def get_klines(self, symbol: str, interval: str, limit: int = 50) -> list:
        raise NotImplementedError

    def get_price(self, symbol: str) -> float:
        raise NotImplementedError
