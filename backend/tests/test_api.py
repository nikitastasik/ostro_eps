"""Сквозной тест: старт приложения на SQLite с реальными данными региона и seed."""

import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./test_e2e.db")
os.environ["WORKER_ENABLED"] = "false"
os.environ.setdefault("SEED_ON_START", "true")


@pytest.fixture(scope="module")
def client():
    db = os.environ["DATABASE_URL"].replace("sqlite:///", "")
    if os.path.exists(db):
        os.remove(db)
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
    if os.path.exists(db):
        os.remove(db)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_meta_and_hotels(client):
    m = client.get("/api/meta").json()
    assert m["hotels"] > 100 and m["characteristics"] >= 20
    hotels = client.get("/api/hotels").json()["items"]
    assert len(hotels) == m["hotels"]


def test_search_explains(client):
    r = client.post("/api/search", json={"query": "тихий номер с видом на закат недалеко от моря"}).json()
    assert {c["key"] for c in r["query"]["criteria"]} == {"quietness", "sunset_view", "beach_access"}
    top = r["results"][0]
    assert top["score"] > 0 and len(top["needs"]) == 3
    assert all(n["text"] for n in top["needs"])


def test_hotel_card_and_view(client):
    hid = client.get("/api/hotels").json()["items"][0]["id"]
    card = client.get(f"/api/hotels/{hid}").json()
    assert card["groups"]
    v = client.get(f"/api/hotels/{hid}/view").json()
    assert len(v["azimuths"]) == len(v["horizon"]) and len(v["suns"]) == 12


def test_collect_run_logs_steps(client):
    hid = client.get("/api/hotels").json()["items"][1]["id"]
    run = client.post(f"/api/hotels/{hid}/collect", json={"char_keys": ["beach_access", "night_services"]}).json()
    assert run["status"] == "done"
    detail = client.get(f"/api/runs/{run['id']}").json()
    kinds = {s["kind"] for s in detail["steps"]}
    assert {"plan", "tool", "result"} <= kinds


def test_hotelier_flow(client):
    assert client.post("/api/auth/login", json={"login": "hotelier", "password": "wrong"}).status_code == 401
    token = client.post("/api/auth/login", json={"login": "hotelier", "password": "hotelier123"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    qs = client.get("/api/questions?status=open").json()["items"]
    q = next(x for x in qs if "pets_policy" in x["char_keys"])
    prev = client.post(f"/api/hotelier/questions/{q['id']}/answer", json={"text": "Можно с собаками до 10 кг, 500 руб в сутки", "preview": True}, headers=auth).json()
    assert prev["preview"]["pets_policy"]["fields"]["max_weight_kg"] == 10
    res = client.post(f"/api/hotelier/questions/{q['id']}/answer", json={"text": "Можно с собаками до 10 кг, 500 руб в сутки"}, headers=auth).json()
    assert res["question"]["status"] == "answered"
    card = client.get(f"/api/hotels/{q['hotel_id']}").json()
    pets = next(i for g in card["groups"] for i in g["items"] if i["key"] == "pets_policy")
    assert pets["value"]["allowed"] is True and "hotelier" in pets["sources"]


def test_compile_characteristic(client):
    r = client.post("/api/characteristics/compile", json={"text": "Банкомат в пределах 300 м"}).json()
    assert r["spec"]["recipe"][0]["tool"] == "osm.poi_nearby" and not r["errors"]


def test_metrics(client):
    m = client.get("/api/metrics").json()
    assert 0 < m["coverage"] <= 1
    assert m["eval"]["sun"]["max_azimuth_error_deg"] < 0.5


def test_conflict_clarification_cycle(client):
    token = client.post("/api/auth/login", json={"login": "hotelier", "password": "hotelier123"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    # отель, где расчёт видит море только с 4-го этажа и выше (допуск сравнения — ±2 этажа) и ждёт подтверждения
    qs = client.get("/api/questions?status=open&limit=2000").json()["items"]
    target = None
    for q in qs:
        if q["char_keys"] != ["sea_view"] or q["reason"] != "verify":
            continue
        card = client.get(f"/api/hotels/{q['hotel_id']}").json()
        sea = next(i for g in card["groups"] for i in g["items"] if i["key"] == "sea_view")
        if sea["value"] and sea["value"].get("available") and (sea["value"].get("min_floor") or 1) >= 4:
            target = (q, sea["value"]["min_floor"])
            break
    assert target, "в seed должен быть отель с видом на море только с верхних этажей"
    q, floor = target
    # отельер приукрашивает → противоречие и уточняющий вопрос
    client.post(f"/api/hotelier/questions/{q['id']}/answer", json={"text": "Море видно из всех номеров!"}, headers=auth)
    conflicts = [c for c in client.get("/api/conflicts?status=open&limit=500").json()["items"]
                 if c["hotel_id"] == q["hotel_id"] and c["char_key"] == "sea_view"]
    assert conflicts
    clar = [x for x in client.get(f"/api/questions?status=open&hotel_id={q['hotel_id']}").json()["items"]
            if x["reason"] == "conflict" and "sea_view" in x["char_keys"]]
    assert clar, "арбитр должен задать уточняющий вопрос"
    # отельер уточняет — противоречие снимается
    client.post(f"/api/hotelier/questions/{clar[0]['id']}/answer", json={"text": f"Да, море видно начиная с {floor} этажа"},
                headers=auth)
    still = [c for c in client.get("/api/conflicts?status=open&limit=500").json()["items"]
             if c["hotel_id"] == q["hotel_id"] and c["char_key"] == "sea_view"]
    assert not still


def test_runtime_texts_are_clean(client):
    """Корректор по текстам поиска, карточек и сравнения: без «0 м», «≈0 мин», непарных кавычек и т. п."""
    from app.nlp.textlint import anomalies

    bad = []
    for q in client.get("/api/meta").json()["examples"]:
        r = client.post("/api/search", json={"query": q, "limit": 400}).json()
        for res in r["results"]:
            texts = [n["text"] for n in res["needs"]] + [res["summary"], (res.get("room") or {}).get("text", "")]
            bad += [(q, res["hotel"]["name"], t, anomalies(t)) for t in texts if anomalies(t)]
    ids = [h["id"] for h in client.get("/api/hotels").json()["items"][:6]]
    cmp = client.get("/api/compare", params={"ids": ",".join(ids)}).json()
    bad += [("compare", row["title"], c["summary"], anomalies(c["summary"])) for row in cmp["rows"] for c in row["cells"]
            if anomalies(c["summary"])]
    assert not bad, bad[:5]


def test_view_rays_for_map(client):
    """Лучи из окна: сектор окна — ровно 120°, сводка с числами, у зданий-заслонов есть контуры."""
    import re

    hid = client.get("/api/hotels").json()["items"][0]["id"]
    v = client.get(f"/api/hotels/{hid}/view").json()
    assert v["window"]["lat"] and len(v["rays"]) == len(v["azimuths"])
    assert sum(v["ray_counts"].values()) == 120 == sum(1 for r in v["rays"] if r["win"])
    assert v["rays_caption"].startswith("Из 120° обзора:")
    assert sum(int(x) for x in re.findall(r"(\d+)° —", v["rays_caption"])) == 120
    for b in v["blockers"]:
        assert len(b["polygon"]) >= 4 and b["deg"] >= 2 and "от окна" in b["label"]


def test_booking_wish(client):
    """Пожелание к бронированию: сторона и этаж из совета, просьбы, вопросы о неизвестном; тексты чистые."""
    from app.nlp.textlint import anomalies

    q = "тихий номер с видом на закат недалеко от моря"
    top = client.post("/api/search", json={"query": q, "limit": 3}).json()["results"][0]
    w = client.get(f"/api/hotels/{top['hotel']['id']}/wish", params={"q": q}).json()
    assert w["text"].startswith("Здравствуйте!") and w["text"].endswith("Спасибо!")
    assert top["room"]["facade_text"].split()[0][:5] in w["text"]
    assert "закат" in w["text"] and w["method"] == "rules" and not anomalies(w["text"])
    q2 = "выспаться после ночного перелёта: плотные шторы и тишина"
    unknown = next(r for r in client.post("/api/search", json={"query": q2, "limit": 400}).json()["results"]
                   if any(n["key"] == "blackout_curtains" and n["status"] == "unknown" for n in r["needs"]))
    w2 = client.get(f"/api/hotels/{unknown['hotel']['id']}/wish", params={"q": q2}).json()
    assert any("шторы" in x for x in w2["questions"]) and "?" in w2["text"]


def test_compare_under_query(client):
    """Сравнение из выдачи: первым — строки по пожеланиям запроса со статусами и победителем."""
    q = "тихий номер с видом на закат недалеко от моря"
    ids = [x["hotel"]["id"] for x in client.post("/api/search", json={"query": q, "limit": 3}).json()["results"]]
    d = client.get("/api/compare", params={"ids": ",".join(ids), "q": q}).json()
    assert {r["key"] for r in d["fit_rows"]} == {"quietness", "sunset_view", "beach_access"}
    assert all(c["status"] in {"match", "conditional", "partial", "miss", "unknown"} for r in d["fit_rows"] for c in r["cells"])
    assert len(d["fit"]) == 3 and all(f["room"] for f in d["fit"])
    assert all(c["short"] and len(c["short"]) <= 60 for r in d["rows"] for c in r["cells"])
    plain = client.get("/api/compare", params={"ids": ",".join(ids)}).json()
    assert plain["fit_rows"] == [] and plain["rows"]
