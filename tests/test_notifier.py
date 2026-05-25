from app.notifier import should_notify, format_notification


def test_notify_on_drop_enabled():
    assert should_notify(old=100.0, new=90.0, notify_on_drop=True, target_price=None, target_pct=None)


def test_no_notify_when_disabled_and_no_target():
    assert not should_notify(old=100.0, new=90.0, notify_on_drop=False, target_price=None, target_pct=None)


def test_no_notify_when_price_rises():
    assert not should_notify(old=80.0, new=90.0, notify_on_drop=True, target_price=None, target_pct=None)


def test_notify_target_price():
    assert should_notify(old=60.0, new=49.0, notify_on_drop=False, target_price=50.0, target_pct=None)


def test_no_notify_target_price_not_reached():
    assert not should_notify(old=60.0, new=51.0, notify_on_drop=False, target_price=50.0, target_pct=None)


def test_notify_target_percentage():
    # drops 15%, threshold 10%
    assert should_notify(old=100.0, new=85.0, notify_on_drop=False, target_price=None, target_pct=10.0)


def test_no_notify_target_percentage_not_reached():
    # drops 5%, threshold 10%
    assert not should_notify(old=100.0, new=95.0, notify_on_drop=False, target_price=None, target_pct=10.0)


def test_format_notification_contains_prices():
    msg = format_notification(name="Teclado", old_price=89.99, new_price=74.99, url="https://amazon.es/dp/X")
    assert "Teclado" in msg
    assert "74,99" in msg
    assert "89,99" in msg
    assert "https://amazon.es" in msg
