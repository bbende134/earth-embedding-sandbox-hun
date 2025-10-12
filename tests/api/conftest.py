import os
import sys

sys.path.insert(0, os.path.dirname(__file__) + "/../..")

from fastapi.testclient import TestClient
from pytest import fixture


@fixture()
def client():
    from api.app import app

    return TestClient(app)
