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
