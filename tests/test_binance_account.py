
from app.market_data.binance_client import BinanceMarketClient


def test_binance_testnet_account():
    client = BinanceMarketClient()

    try:
        account = client.get_account()

        assert "balances" in account
        assert isinstance(account["balances"], list)

        balances = client.get_balances()
        print(f"Activos con saldo: {len(balances)}")

        for balance in balances:
            if balance["asset"] in {"USDT", "BTC", "ETH", "BNB"}:
                print(
                    f'{balance["asset"]}: '
                    f'libre={balance["free"]}, '
                    f'bloqueado={balance["locked"]}'
                )
    finally:
        client.close()


def test_account_requires_credentials(monkeypatch):
    monkeypatch.setattr(
        "app.market_data.binance_client.os.getenv",
        lambda key, default="": (
            "https://testnet.binance.vision"
            if key == "BINANCE_BASE_URL"
            else ""
        ),
    )

    client = BinanceMarketClient()

    try:
        try:
            client.get_account()
            assert False, "Debió rechazar las credenciales vacías"
        except RuntimeError as exc:
            assert "credenciales" in str(exc)
    finally:
        client.close()
