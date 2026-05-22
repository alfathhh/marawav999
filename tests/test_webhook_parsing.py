from app.main import _is_outgoing_message, _is_recent_bot_echo, _remember_bot_message


def test_outgoing_gowa_payload_is_ignored():
    payload = {
        "event": "message",
        "payload": {
            "from": "6281@s.whatsapp.net",
            "message": "balasan bot",
            "key": {"fromMe": True},
        },
    }

    assert _is_outgoing_message(payload)


def test_incoming_gowa_payload_is_not_ignored():
    payload = {
        "event": "message",
        "payload": {
            "from": "6281@s.whatsapp.net",
            "message": "jumlah penduduk",
            "key": {"fromMe": False},
        },
    }

    assert not _is_outgoing_message(payload)


def test_recent_bot_echo_is_ignored():
    cache = {}
    _remember_bot_message(cache, "6281", "Halo\n\n1. Menu")

    assert _is_recent_bot_echo(cache, "6281", "Halo 1. Menu")
