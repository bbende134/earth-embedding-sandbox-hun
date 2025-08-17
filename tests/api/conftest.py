from fastapi.testclient import TestClient
from pytest import fixture


@fixture()
def client():
    from app import app

    return TestClient(app)
