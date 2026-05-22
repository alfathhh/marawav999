from app.conversation.intent_parser import RuleBasedIntentParser
from app.models import Intent


def test_rule_based_intent_parser_examples():
    parser = RuleBasedIntentParser()

    assert parser.classify("1") == Intent.DATA_REQUEST
    assert parser.classify("minta data penduduk") == Intent.DATA_REQUEST
    assert parser.classify("saya mau konsultasi statistik") == Intent.CONSULTATION
    assert parser.classify("mau admin") == Intent.ADMIN
    assert parser.classify("keluar") == Intent.EXIT
    assert parser.classify("indeks pembangunan manusia") == Intent.DATA_REQUEST
    assert parser.classify("data ipm") == Intent.DATA_REQUEST
    assert parser.classify("publikasi luas lahan") == Intent.DATA_REQUEST
    assert parser.extract_data_query("minta data") == ""
    assert parser.extract_data_query("publikasi luas lahan") == "publikasi luas lahan"


def test_ambiguous_input():
    parser = RuleBasedIntentParser()

    assert parser.classify("halo") == Intent.AMBIGUOUS
