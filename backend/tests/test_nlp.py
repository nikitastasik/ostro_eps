from app.agents.compiler import compile_text
from app.knowledge.registry import load_builtin
from app.nlp.answer_parser import parse_answer
from app.nlp.query_parser import parse_query

SPECS = {s["key"]: s for s in load_builtin()}


def keys(q: str) -> set[str]:
    return {c.key for c in parse_query(q, SPECS).criteria}


def test_query_parser_core_cases():
    assert keys("тихий номер с видом на закат недалеко от моря") == {"quietness", "sunset_view", "beach_access"}
    assert keys("с собакой, чтобы рядом был парк для выгула") == {"pets_policy", "pets_walk"}
    assert "nightlife" not in keys("тихое место без ночных клубов рядом")


def test_query_numbers_and_months():
    crit = parse_query("до пляжа не больше 5 минут пешком", SPECS).criteria
    assert any(c.field == "minutes_to" and c.value == 5 for c in crit)
    crit = parse_query("закат над морем в октябре", SPECS).criteria
    assert any(c.key == "sunset_view" and c.month == 10 for c in crit)


def test_answer_parser_pets():
    r = parse_answer(SPECS["pets_policy"], "Да, с собаками можно, до 10 кг, доплата 500 р в сутки. Кошек тоже берём.")
    f = r["fields"]
    assert f["allowed"] is True and set(f["animals"]) == {"dog", "cat"} and f["max_weight_kg"] == 10
    assert f["fee_rub"] == 500 and f["fee_unit"] == "night"
    assert parse_answer(SPECS["pets_policy"], "К сожалению, с животными не размещаем.")["fields"] == {"allowed": False}


def test_answer_parser_floors_and_windows():
    assert parse_answer(SPECS["sea_view"], "Вид на море с 4 этажа, номера на юго-запад")["fields"]["min_floor"] == 4
    assert parse_answer(SPECS["building_levels"], "У нас девятиэтажное здание")["fields"]["levels"] == 9
    assert parse_answer(SPECS["soundproofing"], "Специальной шумоизоляции нет, обычные окна")["fields"]["windows"] == "standard"


def test_compiler_rules():
    spec, method, errs = compile_text("Есть ли круглосуточная аптека в 10 минутах пешком", set())
    assert not errs and method == "rules"
    step = spec["recipe"][0]
    assert step["tool"] == "osm.poi_walk" and step["params"]["categories"] == ["pharmacy"]
    assert step["params"]["filter"] == "24_7" and step["params"]["max_minutes"] == 10
    spec, _, _ = compile_text("Есть ли в номере чайник", set())
    assert spec["recipe"][0]["tool"] == "hotelier.ask" and spec["question"]["text"].endswith("?")
