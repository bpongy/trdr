import pytest

from trdr.exchange import BinanceSandbox


class FakeCcxt:
    def __init__(self):
        self.orders = []

    def load_markets(self):
        pass

    def amount_to_precision(self, symbol, amount):
        return f"{int(amount * 10_000) / 10_000:.4f}"  # arrondi inférieur au pas de 0,0001

    def create_market_buy_order_with_cost(self, symbol, cost):
        self.orders.append(("buy", cost))
        return {"filled": 0.01, "average": 2000.0, "cost": 20.0, "fees": [{"currency": "ETH", "cost": 0.00001}]}

    def create_market_sell_order(self, symbol, amount):
        self.orders.append(("sell", amount))
        return {"filled": amount, "average": 2100.0, "cost": amount * 2100.0, "fees": [{"currency": "USDT", "cost": 0.02}]}


@pytest.fixture
def exchange():
    instance = BinanceSandbox.__new__(BinanceSandbox)
    instance._trade = instance._data = FakeCcxt()
    return instance


@pytest.mark.parametrize(("env", "host"), [("testnet", "testnet.binance.vision"), ("demo", "demo-api.binance.com")])
def test_only_virtual_environments(env, host):
    client = BinanceSandbox("key", "secret", env)
    assert host in client._trade.urls["api"]["private"]
    assert "api.binance.com/api" not in client._trade.urls["api"]["private"].replace("demo-api.", "")


def test_live_environment_is_refused():
    with pytest.raises(ValueError):
        BinanceSandbox("key", "secret", "live")


def test_live_market_data_client_has_no_keys():
    client = BinanceSandbox("key", "secret", "testnet", market_data_source="live")
    assert client._data is not client._trade
    assert not client._data.apiKey and not client._data.secret


def test_buy_quantity_is_net_of_fees(exchange):
    fill = exchange.market_buy("ETH/USDT", 20.0)
    assert fill.quantity == pytest.approx(0.00999)
    assert (fill.price, fill.cost) == (2000.0, 20.0)


def test_sell_rounds_down_and_deducts_fees(exchange):
    fill = exchange.market_sell("ETH/USDT", 0.00999)
    assert exchange._trade.orders == [("sell", 0.0099)]
    assert fill.cost == pytest.approx(0.0099 * 2100.0 - 0.02)
